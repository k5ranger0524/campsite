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
import json
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


def analyze(html, path, rendered=False):
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
    if rendered:
        print("  → ブラウザで描画した後のHTMLです。この内容からアダプタを書けます。")
        print("     captures/<name>.api.json に空き状況のAPIが記録されていれば、")
        print("     そちらを直接叩く方が軽くて安定します。")
    elif len(html) < 2000 or (hits and not re.search(r"<table[ >]", html, re.I)):
        print("  → JavaScriptで描画している可能性があります。")
        print("     --render を付けて取り直すと判断できます。")
    else:
        print("  → サーバ側で描画されているようです。このHTMLからアダプタを書けます。")
    print()


MAX_BODY = 200_000        # 1レスポンスあたり保存する上限
MAX_RECORDED = 40


def _find_chromium():
    """インストール済みのChromiumを探す。見つからなければ None（playwright任せ）。"""
    env = os.environ.get("CHROMIUM_EXECUTABLE")
    if env and os.path.exists(env):
        return env
    root = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    if not root or not os.path.isdir(root):
        return None
    candidates = []
    for entry in sorted(os.listdir(root), reverse=True):
        for rel in ("chrome-linux/chrome", "chrome-linux/headless_shell"):
            path = os.path.join(root, entry, rel)
            if os.path.exists(path):
                candidates.append(path)
    return candidates[0] if candidates else None


def render_with_browser(url, name, wait_selector=None, clicks=(), wait_ms=4000):
    """JavaScript描画ページ用。Playwright で描画してからHTMLを取る。

    同時に以下も残す:
      captures/<name>.png       画面のスクリーンショット（何が表示されているかの確認用）
      captures/<name>.api.json  ページが裏で叩いたAPI（空き状況がここに居ることが多い）
    """
    try:
        from playwright.sync_api import sync_playwright
        from playwright.sync_api import Error as PWError
    except ImportError:
        raise MonitorError(
            "Playwright が入っていません: pip install playwright && playwright install chromium"
        )

    launch_kwargs = {}
    exe = _find_chromium()
    if exe:
        # 環境にインストール済みのChromiumを使う（playwrightのバージョンと
        # ブラウザのビルド番号がずれていても動かすため）
        log(f"Chromium を使います: {exe}")
        launch_kwargs["executable_path"] = exe

    recorded = []

    def on_response(resp):
        if len(recorded) >= MAX_RECORDED:
            return
        try:
            ctype = (resp.headers or {}).get("content-type", "")
            req = resp.request
            if req.resource_type not in ("xhr", "fetch") and "json" not in ctype:
                return
            entry = {
                "url": resp.url,
                "method": req.method,
                "status": resp.status,
                "content_type": ctype,
                "resource_type": req.resource_type,
            }
            if req.post_data:
                entry["post_data"] = req.post_data[:2000]
            try:
                body = resp.text()
                entry["truncated"] = len(body) > MAX_BODY
                entry["body"] = body[:MAX_BODY]
            except PWError:
                entry["body"] = "(取得できませんでした)"
            recorded.append(entry)
        except Exception:
            pass          # 記録は補助情報。失敗しても本体の取得は続ける

    with sync_playwright() as p:
        browser = p.chromium.launch(**launch_kwargs)
        page = browser.new_page(
            user_agent=DEFAULT_HEADERS["User-Agent"],
            locale="ja-JP",
            viewport={"width": 1280, "height": 2000},
        )
        page.on("response", on_response)

        log(f"ブラウザで開きます: {url}")
        page.goto(url, wait_until="networkidle", timeout=60000)

        for selector in clicks:
            log(f"クリックします: {selector}")
            try:
                page.click(selector, timeout=15000)
                page.wait_for_load_state("networkidle", timeout=30000)
            except PWError as exc:
                log(f"  クリックできませんでした（続行します）: {exc}")

        if wait_selector:
            log(f"要素を待ちます: {wait_selector}")
            try:
                page.wait_for_selector(wait_selector, timeout=30000)
            except PWError as exc:
                log(f"  要素が出ませんでした（続行します）: {exc}")
        else:
            page.wait_for_timeout(wait_ms)

        html = page.content()

        os.makedirs(OUT_DIR, exist_ok=True)
        shot = os.path.join(OUT_DIR, f"{name}.png")
        try:
            page.screenshot(path=shot, full_page=True)
            log(f"スクリーンショット: {shot}")
        except PWError as exc:
            log(f"スクリーンショットを撮れませんでした: {exc}")

        browser.close()

    if recorded:
        api_path = os.path.join(OUT_DIR, f"{name}.api.json")
        with open(api_path, "w", encoding="utf-8") as f:
            json.dump(recorded, f, ensure_ascii=False, indent=2)
        log(f"APIレスポンス {len(recorded)} 件を記録: {api_path}")
        for r in recorded[:10]:
            log(f"  {r['method']:<5} {r['status']} {r['url'][:100]}")
    else:
        log("ページが叩いたAPIは記録されませんでした")

    return html


def main():
    ap = argparse.ArgumentParser(description="監視対象ページのHTMLを保存する")
    ap.add_argument("--url", help="取得するURL")
    ap.add_argument("--name", help="保存名（captures/<name>.html）")
    ap.add_argument("--render", action="store_true", help="ブラウザで描画してから保存")
    ap.add_argument("--wait-selector", help="--render のとき、この要素が出るまで待つ")
    ap.add_argument("--click", action="append", default=[], metavar="SELECTOR",
                    help="--render のとき、取得前にクリックする要素（複数可）")
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
            html = render_with_browser(args.url, args.name, args.wait_selector, args.click)
        else:
            log(f"取得します: {args.url}")
            html = fetch_html(args.url)

        os.makedirs(OUT_DIR, exist_ok=True)
        path = os.path.join(OUT_DIR, f"{args.name}.html")
        with open(path, "w", encoding="utf-8") as f:
            f.write(html)
        log(f"保存しました: {path} ({len(html)} 文字)")
        analyze(html, path, rendered=args.render)
        return 0

    except MonitorError as exc:
        log(f"エラー: {exc}")
        return 1
    except OSError as exc:
        log(f"エラー: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
