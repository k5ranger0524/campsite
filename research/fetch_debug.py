#!/usr/bin/env python3
"""赤城山オートキャンプ場 予約サイトの生HTMLを取得して debug.html に保存する。

調査フェーズ用のスクリプト。空室判定ロジックは一切含まない（何も推測しない）。
取得した生HTMLをそのまま保存し、解析は analyze_debug.py に任せる。

使い方:
    pip install -r requirements.txt
    python3 fetch_debug.py                    # 既定の日付 (2026-09-19)
    python3 fetch_debug.py --date 2026-09-20
"""

import argparse
import sys
import time

import requests

BASE = "https://reserve.489ban.net/client/autocamp-akagi/0"
TARGET = BASE + "/plan/availability/room/stay?date={date}"

# 通常のブラウザの User-Agent を名乗る
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,image/apng,*/*;q=0.8"
    ),
    "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
    "Upgrade-Insecure-Requests": "1",
}


def get(session, url, referer=None):
    """ネットワークエラー時のみ指数バックオフでリトライする。"""
    headers = dict(HEADERS)
    if referer:
        headers["Referer"] = referer
    delay = 2
    last = None
    for attempt in range(5):
        try:
            return session.get(url, headers=headers, timeout=30, allow_redirects=True)
        except requests.RequestException as exc:
            last = exc
            if attempt == 4:
                break
            print(f"  取得失敗 ({exc.__class__.__name__}): {delay}秒後に再試行", file=sys.stderr)
            time.sleep(delay)
            delay *= 2
    raise SystemExit(f"取得に失敗しました: {last}")


def decode(resp):
    """Content-Type に charset が無い場合 requests は ISO-8859-1 と誤認するため補正する。"""
    ctype = resp.headers.get("Content-Type", "")
    if "charset=" not in ctype.lower():
        resp.encoding = resp.apparent_encoding or "utf-8"
    return resp.text


def report(label, resp):
    print(f"[{label}]")
    print(f"  status        : {resp.status_code}")
    print(f"  final url     : {resp.url}")
    print(f"  content-type  : {resp.headers.get('Content-Type')}")
    print(f"  server        : {resp.headers.get('Server')}")
    print(f"  bytes         : {len(resp.content)}")
    if resp.history:
        print(f"  redirects     : {[h.status_code for h in resp.history]}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default="2026-09-19")
    ap.add_argument("--out", default="debug.html")
    ap.add_argument(
        "--no-warmup",
        action="store_true",
        help="トップページへの先行アクセス（セッション確立）を省略する",
    )
    args = ap.parse_args()

    url = TARGET.format(date=args.date)
    session = requests.Session()

    # 予約システムはセッションcookie前提のことがあるため、まずプラン一覧に触れておく。
    # これが必要かどうかも検証対象なので、--no-warmup で比較できるようにしている。
    referer = None
    if not args.no_warmup:
        warm = get(session, BASE + "/plan")
        report("warmup " + BASE + "/plan", warm)
        referer = warm.url

    resp = get(session, url, referer=referer)
    report("target " + url, resp)
    print(f"  cookies       : {sorted(session.cookies.keys())}")

    html = decode(resp)
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(html)
    with open(args.out + ".raw", "wb") as f:
        f.write(resp.content)

    print()
    print(f"保存しました: {args.out} ({len(html)} 文字) / {args.out}.raw (生バイト列)")
    print("次に実行してください: python3 analyze_debug.py")


if __name__ == "__main__":
    main()
