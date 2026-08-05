"""HTTP取得。監視対象に依存しない。"""

import os
import socket
import ssl
import tempfile
import time

import requests

from .util import MonitorError, log

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

DEFAULT_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,*/*;q=0.8"
    ),
    "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
    "Upgrade-Insecure-Requests": "1",
}


# 中間証明書を送ってこないサーバ用の、ホストごとの補完済みCAバンドル
_CA_BUNDLE_CACHE = {}


def _peer_certificate(host, port=443, timeout=20):
    """サーバが提示した証明書をDERで取る。

    ここでは検証しない。証明書に書かれた「発行者の在り処(AIA)」を読むためだけに使う。
    実際の通信は、この後に取得した中間証明書を足したうえで通常どおり検証する。
    """
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    with socket.create_connection((host, port), timeout=timeout) as sock:
        with ctx.wrap_socket(sock, server_hostname=host) as tls:
            return tls.getpeercert(binary_form=True)


def _issuer_bundle(host, port=443):
    """中間証明書を補ったCAバンドルのパスを返す。作れなければ None。

    ブラウザは証明書の AIA(caIssuers) を見て足りない中間証明書を取りに行く。
    requests はそれをしないため、サーバが中間証明書を送ってこないと
    「unable to get local issuer certificate」で失敗する。同じことを肩代わりする。

    信頼を緩めるわけではない: 取ってきた中間証明書が既存のルートに繋がらなければ、
    このあとの検証は通らない。
    """
    cache_key = (host, port)
    if cache_key in _CA_BUNDLE_CACHE:
        return _CA_BUNDLE_CACHE[cache_key]

    _CA_BUNDLE_CACHE[cache_key] = None     # 失敗しても繰り返し試さない
    try:
        import certifi
        from cryptography import x509
        from cryptography.hazmat.primitives.serialization import Encoding
        from cryptography.x509.oid import AuthorityInformationAccessOID, ExtensionOID
    except Exception as exc:
        # 壊れたインストールは ImportError 以外も投げる。ここで監視を止めない。
        log(f"  中間証明書の補完に必要なライブラリを読み込めません: {exc}")
        return None

    try:
        cert = x509.load_der_x509_certificate(_peer_certificate(host, port))
        aia = cert.extensions.get_extension_for_oid(
            ExtensionOID.AUTHORITY_INFORMATION_ACCESS).value
        urls = [
            d.access_location.value for d in aia
            if d.access_method == AuthorityInformationAccessOID.CA_ISSUERS
        ]
    except Exception as exc:
        log(f"  証明書から発行者情報を読めませんでした: {exc}")
        return None

    extra = []
    for url in urls:
        try:
            resp = requests.get(url, timeout=20)
            resp.raise_for_status()
            data = resp.content
            try:
                issuer = x509.load_der_x509_certificate(data)
            except ValueError:
                issuer = x509.load_pem_x509_certificate(data)
            extra.append(issuer.public_bytes(Encoding.PEM))
            log(f"  中間証明書を取得しました: {issuer.subject.rfc4514_string()}")
        except Exception as exc:
            log(f"  中間証明書を取得できませんでした ({url}): {exc}")

    if not extra:
        return None

    fd, path = tempfile.mkstemp(prefix=f"ca-{host}-", suffix=".pem")
    with os.fdopen(fd, "wb") as f:
        with open(certifi.where(), "rb") as base:
            f.write(base.read())
        f.write(b"\n")
        for pem in extra:
            f.write(pem)

    _CA_BUNDLE_CACHE[cache_key] = path
    return path


def fetch_html(url, headers=None, timeout=30, attempts=4):
    """指数バックオフ付きで取得する。失敗しきったら MonitorError。"""
    merged = dict(DEFAULT_HEADERS)
    if headers:
        merged.update(headers)

    verify = True
    tried_bundle = False
    delay = 2
    last = None
    attempt = 0

    def backoff(exc):
        """通信の一時不調に対する再試行。回数を消費する。"""
        nonlocal attempt, delay
        attempt += 1
        if attempt < attempts:
            log(f"  取得失敗 ({exc.__class__.__name__}): {delay}秒後に再試行")
            time.sleep(delay)
            delay *= 2

    while attempt < attempts:
        try:
            resp = requests.get(url, headers=merged, timeout=timeout, verify=verify)
            resp.raise_for_status()
            if "charset=" not in resp.headers.get("Content-Type", "").lower():
                resp.encoding = resp.apparent_encoding or "utf-8"
            return resp.text
        except requests.exceptions.SSLError as exc:
            last = exc
            # 中間証明書を送ってこないサーバなら、それを補ってやり直す。
            # これは待てば直る類ではなく一度きりの是正なので、試行回数は消費しない。
            if not tried_bundle:
                tried_bundle = True
                parsed = requests.utils.urlparse(url)
                host, port = parsed.hostname, parsed.port or 443
                log("  SSL検証に失敗。中間証明書の補完を試みます")
                bundle = _issuer_bundle(host, port) if host else None
                if bundle:
                    verify = bundle
                    continue
            backoff(exc)
        except requests.RequestException as exc:
            last = exc
            backoff(exc)

    raise MonitorError(f"ページ取得に失敗しました: {last}")
