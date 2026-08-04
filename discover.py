#!/usr/bin/env python3
"""監視対象の候補を洗い出す。

targets.yml に何を書けばよいか（区画ID・部屋IDなど）を調べるためのツール。
既知の予約システムなら、HTMLを自分で読む調査は不要になる。

使い方:
    python3 discover.py --adapter ban489 --facility autocamp-akagi --date 2026-09-19
    python3 discover.py --adapter ban489 --file debug.html --date 2026-09-19

出力をそのまま targets.yml の items に貼れる形でも表示する。
"""

import argparse
import sys

import adapters
from core import status as st
from core.fetch import fetch_html
from core.util import MonitorError, log


def main():
    ap = argparse.ArgumentParser(description="監視対象の候補を洗い出す")
    ap.add_argument("--adapter", required=True, help="アダプタ名（例: ban489）")
    ap.add_argument("--date", help="調べる日付 (YYYY-MM-DD)")
    ap.add_argument("--file", help="実サイトの代わりにローカルHTMLを読む")
    ap.add_argument(
        "--config", action="append", default=[], metavar="KEY=VALUE",
        help="アダプタに渡す設定。例: --config facility=autocamp-akagi",
    )
    ap.add_argument("--facility", help="--config facility=... の省略形")
    ap.add_argument("--available-only", action="store_true", help="空きありだけ表示")
    args = ap.parse_args()

    config = {}
    for pair in args.config:
        if "=" not in pair:
            print(f"--config は KEY=VALUE 形式で指定してください: {pair}", file=sys.stderr)
            return 1
        k, v = pair.split("=", 1)
        config[k] = v
    if args.facility:
        config["facility"] = args.facility

    try:
        adapter = adapters.get(args.adapter)
        if not hasattr(adapter, "discover"):
            raise MonitorError(f"アダプタ '{args.adapter}' は discover() に対応していません")

        if args.file:
            log(f"ローカルファイルを読み込みます: {args.file}")
            with open(args.file, encoding="utf-8") as f:
                html = f.read()
        else:
            url = adapter.build_url(config, args.date)
            log(f"取得します: {url}")
            html = fetch_html(url)

        found = adapter.discover(html, config, args.date, warn=lambda m: log(f"警告: {m}"))
    except MonitorError as exc:
        log(f"エラー: {exc}")
        return 1
    except OSError as exc:
        log(f"エラー: ファイルを読めません: {exc}")
        return 1

    if args.available_only:
        found = [f for f in found if f["status"] == st.AVAILABLE]

    if not found:
        log("該当する対象が見つかりませんでした")
        return 1

    print()
    print(f"見つかった対象: {len(found)} 件" + ("（空きありのみ）" if args.available_only else ""))
    print("=" * 70)
    for f in found:
        print(f"  {f['key']:<16} {st.label(f['status']):<8} {f['name']}")

    print()
    print("targets.yml に貼る形:")
    print("-" * 70)
    print("    items:")
    for i, f in enumerate(found, 1):
        print(f"      A{i}: {f['key']}          # {f['name']}")
    print()
    print("※ キー（A1, A2...）は通知に出る短い名前です。分かりやすい名前に変えてください。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
