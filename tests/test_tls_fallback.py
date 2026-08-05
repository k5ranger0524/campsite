#!/usr/bin/env python3
"""中間証明書を送ってこないサーバから取得できることを確かめる。

haneda-p4.jp が実際にこの状態で、ブラウザでは開けるが requests では
「unable to get local issuer certificate」で失敗した。
core.fetch はブラウザと同じように AIA から中間証明書を取りに行く。

検証を緩めていないことも確かめる:
    - 中間証明書を配らなければ、取得は失敗しなければならない
    - ルートに繋がらない証明書なら、取得は失敗しなければならない

ここでは自前のルートCA・中間CA・サーバ証明書を作り、
「中間証明書を送らないHTTPSサーバ」と「AIAの配布先」を localhost に立てて再現する。

実行: python3 tests/test_tls_fallback.py
"""

import datetime
import http.server
import os
import ssl
import sys
import tempfile
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cryptography import x509                                    # noqa: E402
from cryptography.hazmat.primitives import hashes, serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import rsa         # noqa: E402
from cryptography.x509.oid import AuthorityInformationAccessOID, NameOID  # noqa: E402

import core.fetch as fetch                                        # noqa: E402

PASS = 0
FAIL = 0


def check(desc, cond):
    global PASS, FAIL
    if cond:
        print(f"  ok   : {desc}")
        PASS += 1
    else:
        print(f"  FAIL : {desc}")
        FAIL += 1


def _name(cn):
    return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])


def _key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _sign(builder, key, digest=hashes.SHA256()):
    return builder.sign(key, digest)


def build_chain(aia_url):
    now = datetime.datetime.now(datetime.timezone.utc)
    later = now + datetime.timedelta(days=1)

    root_key, int_key, leaf_key = _key(), _key(), _key()

    root = _sign(
        x509.CertificateBuilder()
        .subject_name(_name("test-root")).issuer_name(_name("test-root"))
        .public_key(root_key.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(now).not_valid_after(later)
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True),
        root_key)

    inter = _sign(
        x509.CertificateBuilder()
        .subject_name(_name("test-intermediate")).issuer_name(root.subject)
        .public_key(int_key.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(now).not_valid_after(later)
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True),
        root_key)

    leaf = _sign(
        x509.CertificateBuilder()
        .subject_name(_name("localhost")).issuer_name(inter.subject)
        .public_key(leaf_key.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(now).not_valid_after(later)
        .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost")]),
                       critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        # ここが肝: 「発行者はここにある」とだけ書き、チェーンは送らない
        .add_extension(x509.AuthorityInformationAccess([
            x509.AccessDescription(
                AuthorityInformationAccessOID.CA_ISSUERS,
                x509.UniformResourceIdentifier(aia_url))
        ]), critical=False),
        int_key)

    return root, inter, leaf, leaf_key


def serve_https(certfile, keyfile):
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            body = b"<html><body>secret page</body></html>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    httpd = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    # 葉証明書だけを読み込む＝中間証明書を送らないサーバの再現
    ctx.load_cert_chain(certfile, keyfile)
    httpd.socket = ctx.wrap_socket(httpd.socket, server_side=True)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


def serve_http(directory):
    class Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *a, **k):
            super().__init__(*a, directory=directory, **k)

        def log_message(self, *a):
            pass

    httpd = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


def main():
    tmp = tempfile.mkdtemp()
    aia_dir = os.path.join(tmp, "aia")
    os.makedirs(aia_dir)

    aia_srv = serve_http(aia_dir)
    aia_url = f"http://127.0.0.1:{aia_srv.server_port}/intermediate.der"

    root, inter, leaf, leaf_key = build_chain(aia_url)

    leaf_pem = os.path.join(tmp, "leaf.pem")
    key_pem = os.path.join(tmp, "leaf.key")
    root_pem = os.path.join(tmp, "root.pem")
    with open(leaf_pem, "wb") as f:
        f.write(leaf.public_bytes(serialization.Encoding.PEM))
    with open(key_pem, "wb") as f:
        f.write(leaf_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption()))
    with open(root_pem, "wb") as f:
        f.write(root.public_bytes(serialization.Encoding.PEM))

    https = serve_https(leaf_pem, key_pem)
    url = f"https://localhost:{https.server_port}/"

    # 信頼の起点をこのテスト用ルートに差し替える（certifi の代わり）
    import certifi
    real_where = certifi.where
    certifi.where = lambda: root_pem

    print("== 1. 中間証明書が配られていなければ、取得は失敗する ==")
    fetch._CA_BUNDLE_CACHE.clear()
    try:
        fetch.fetch_html(url, attempts=1)
        check("失敗する（検証を緩めていない）", False)
    except Exception as exc:
        check("失敗する（検証を緩めていない）", "ページ取得に失敗" in str(exc))

    print("\n== 2. AIAに中間証明書があれば取得できる ==")
    with open(os.path.join(aia_dir, "intermediate.der"), "wb") as f:
        f.write(inter.public_bytes(serialization.Encoding.DER))
    fetch._CA_BUNDLE_CACHE.clear()
    try:
        html = fetch.fetch_html(url, attempts=1)
        check("ページを取得できる", "secret page" in html)
    except Exception as exc:
        check(f"ページを取得できる（{exc}）", False)

    print("\n== 3. ルートに繋がらない証明書は、補完しても失敗する ==")
    other_root, other_inter, _, _ = build_chain(aia_url)
    with open(os.path.join(aia_dir, "intermediate.der"), "wb") as f:
        f.write(other_inter.public_bytes(serialization.Encoding.DER))   # 無関係な中間
    fetch._CA_BUNDLE_CACHE.clear()
    try:
        fetch.fetch_html(url, attempts=1)
        check("失敗する（偽の中間証明書を受け入れない）", False)
    except Exception as exc:
        check("失敗する（偽の中間証明書を受け入れない）", "ページ取得に失敗" in str(exc))

    certifi.where = real_where
    https.shutdown()
    aia_srv.shutdown()

    print("\n" + "=" * 30)
    print(f"  成功 {PASS} / 失敗 {FAIL}")
    print("=" * 30)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
