#!/usr/bin/env python3
"""監視対象ページのHTMLを取得して保存する。新しい対象に対応する最初の一歩。

保存したHTMLを見てアダプタを書く。JavaScriptで描画しているページは
--render を付けるとブラウザで描画してから保存する。

使い方:
    python3 capture.py --url https://example.com/foo --name example
    python3 capture.py --url https://example.com/foo --name example --render
    python3 capture.py --analyze captures/example.html    # 保存済みを調べるだけ
"""

import argparse
import os
import re
import sys

from core.fetch import DEFAULT_HEADERS, fetch_html
from core.util import MonitorError, log

OUT_DIR = "captures"

# JS描画・非同期取得の痕跡
JS_MARKERS = [
    "__NEXT_DATA__", "ReactDOM", "data-reactroot", "Vue.createApp", "new Vue",
    "ng-app", "angular", "knockout", "data-bind", "Alpine.start", "x-data",
    "htmx", "Stimulus", "$.ajax", "XMLHttpRequest", "axios", "fetch(",
]


def analyze(html, path):
    """このページが requests だけで読めるかの手がかりを出す。"""
    print()
    print("=" * 70)
    print(f"取得したHTMLの素性: {path}")
    print("=" * 70)
    print(f"  文字数        : {len(html)}")

    title = re.search(r"<title[^>]*>(.*?)</title>", html, re.S | re.I)
    print(f"  <title>       : {title.group(1).strip() if title else '(なし)'}")

    for tag in ("table", "form", "script", "iframe"):
        print(f"  <{tag}>{' ' * (12 - len(tag))}: {len(re.findall(f'<{tag}[ >]', html, re.I))}")

    hits = [m for m in JS_MARKERS if m in html]
    print(f"  JS痕跡        : {hits if hits else '(検出なし)'}")

    ns = re.findall(r"<noscript[^>]*>(.*?)</noscript>", html, re.S | re.I)
    if ns:
        text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", ns[0])).strip()
        print(f"  <noscript>    : {text[:120]}")

    marks = {c: html.count(c) for c in "○◯●△×✕－満空残" if c in html}
    print(f"  記号文字      : {marks if marks else '(なし)'}")

    # 空き状況ページでよく出る語
    words = {w: html.count(w) for w in ("空車", "満車", "空室", "満室", "予約", "残り")
             if w in html}
    print(f"  関連語        : {words if words else '(なし)'}")

    print()
    if len(html) < 2000 or (hits and not re.search(r"<table[ >]", html, re.I)):
        print("  → JavaScriptで描画している可能性があります。")
        print("     --render を付けて取り直すと判断できます。")
    else:
        print("  → サーバ側で描画されているようです。このHTMLからアダプタを書けます。")
    print()


def render_with_browser(url, wait_selector=None, wait_ms=4000):
    """JavaScript描画ページ用。Playwright で描画してからHTMLを取る。"""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise MonitorError(
            "Playwright が入っていません: pip install playwright && playwright install chromium"
        )

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(
            user_agent=DEFAULT_HEADERS["User-Agent"],
            locale="ja-JP",
            viewport={"width": 1280, "height": 2000},
        )
        log(f"ブラウザで開きます: {url}")
        page.goto(url, wait_until="networkidle", timeout=60000)
        if wait_selector:
            log(f"要素を待ちます: {wait_selector}")
            page.wait_for_selector(wait_selector, timeout=30000)
        else:
            page.wait_for_timeout(wait_ms)
        html = page.content()
        browser.close()
    return html


def main():
    ap = argparse.ArgumentParser(description="監視対象ページのHTMLを保存する")
    ap.add_argument("--url", help="取得するURL")
    ap.add_argument("--name", help="保存名（captures/<name>.html）")
    ap.add_argument("--render", action="store_true", help="ブラウザで描画してから保存")
    ap.add_argument("--wait-selector", help="--render のとき、この要素が出るまで待つ")
    ap.add_argument("--analyze", help="保存済みHTMLを調べるだけ")
    args = ap.parse_args()

    try:
        if args.analyze:
            with open(args.analyze, encoding="utf-8") as f:
                analyze(f.read(), args.analyze)
            return 0

        if not args.url or not args.name:
            ap.error("--url と --name が必要です（または --analyze）")

        if args.render:
            html = render_with_browser(args.url, args.wait_selector)
        else:
            log(f"取得します: {args.url}")
            html = fetch_html(args.url)

        os.makedirs(OUT_DIR, exist_ok=True)
        path = os.path.join(OUT_DIR, f"{args.name}.html")
        with open(path, "w", encoding="utf-8") as f:
            f.write(html)
        log(f"保存しました: {path} ({len(html)} 文字)")
        analyze(html, path)
        return 0

    except MonitorError as exc:
        log(f"エラー: {exc}")
        return 1
    except OSError as exc:
        log(f"エラー: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
