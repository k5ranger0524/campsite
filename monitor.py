#!/usr/bin/env python3
"""空き状況の監視。

targets.yml に書かれた対象を順に確認し、「空きなし」→「空きあり」に変わったものを
ntfy で通知する。空きが続く間は一定時間ごとに再通知し、空きが消えたら履歴をリセットする。

キャンプ場・駐車場・宿など対象の種類は問わない。サイトごとの読み取り方は
adapters/ 以下のモジュールが持ち、それ以外（取得・状態管理・通知）は共通。

役割分担:
    ntfy通知          = 空きが出た（本来の目的）
    ジョブ失敗(exit 1) = 監視が壊れた（異常検知）

使い方:
    python3 monitor.py                          # 全対象を確認
    python3 monitor.py --target akagi-family    # 1つだけ
    python3 monitor.py --list                   # 対象の一覧
    python3 monitor.py --target X --file a.html # ローカルHTMLでパースを確認
    python3 monitor.py --test-notify            # 通知を強制発火して疎通確認

終了コード:
    0  正常終了（通知なし、または通知の送信に成功）
    1  異常終了（設定不備・取得失敗・構造変化・通知送信失敗）。
       失敗した対象の state は更新しない
"""

import argparse
import sys

import adapters
from core import notify as nt
from core import status as st
from core.fetch import fetch_html
from core.state import StateStore, decide_notifications
from core.targets import load_targets
from core.util import MonitorError, log, now_jst


def get_html(target, adapter, args):
    if args.file:
        log(f"  ローカルファイルを読み込みます: {args.file}")
        try:
            with open(args.file, encoding="utf-8") as f:
                return f.read()
        except OSError as exc:
            raise MonitorError(f"ファイルを読めません: {exc}")

    # 独自の取得手順を持つアダプタ（POSTが要る、JS描画など）はそちらを使う
    if hasattr(adapter, "fetch"):
        return adapter.fetch(target.config, target.date)

    url = adapter.build_url(target.config, target.date)
    log(f"  取得します: {url}")
    return fetch_html(url)


def check_target(target, args, store, now):
    """1対象を確認する。例外は呼び出し元で捕捉し、他の対象の確認は続ける。"""
    adapter = adapters.get(target.adapter)
    url = adapter.build_url(target.config, target.date)

    def warn(msg):
        log(f"  警告: {msg}")

    html = get_html(target, adapter, args)
    items = adapter.parse(html, target.config, target.date, warn=warn)
    if not items:
        raise MonitorError("監視対象が0件です")

    for key, info in items.items():
        extra = []
        if info.get("column") is not None:
            extra.append(f"列={info['column']}")
        if info.get("icon"):
            extra.append(f"icon={info['icon']}")
        detail = f" ({', '.join(extra)})" if extra else ""
        log(f"  {key}  {st.label(info['status']):8s}{detail}  {info.get('name') or ''}")

    if args.test_notify:
        log("  --test-notify: 状態に関わらず通知を強制発火します（state は更新しません）")
        nt.deliver(
            nt.build_test_title(target.name),
            nt.build_test_message(target.name, items, url, target.date),
            url,
        )
        return

    previous = store.items_for(target.id)
    if not previous:
        log("  前回の状態がありません（初回確認）")

    new_keys, cont_keys = decide_notifications(items, previous, now, target.repeat_hours)

    if new_keys or cont_keys:
        if new_keys:
            log(f"  空きを検知: {', '.join(new_keys)}")
        if cont_keys:
            log(f"  空きあり継続中（再通知）: {', '.join(cont_keys)}")
        nt.deliver(
            nt.build_alert_title(target.name, new_keys),
            nt.build_alert(target.name, new_keys, cont_keys, items, url,
                           target.date, target.repeat_hours),
            url,
        )
    else:
        log("  通知対象はありません")

    # ここまで来た＝取得もパースも通知も成功。状態を確定させる。
    if args.file and not args.write_state_from_file:
        log("  --file モードのため state は更新しません（--write-state-from-file で上書き可）")
    else:
        store.update(target.id, items, new_keys + cont_keys, now, target.date)
        log(f"  状態を保存しました: {args.state}")


def run(args):
    targets = load_targets(args.targets)

    if args.list:
        for t in targets:
            mark = " " if t.enabled else "×"
            date = t.date or "(日付なし)"
            print(f"{mark} {t.id:<20} {t.adapter:<10} {date:<12} {t.name}")
        return 0

    if args.target:
        targets = [t for t in targets if t.id == args.target]
        if not targets:
            raise MonitorError(f"対象が見つかりません: {args.target}")
    else:
        targets = [t for t in targets if t.enabled]
        if not targets:
            raise MonitorError("有効な対象がありません")

    if args.file and len(targets) > 1:
        raise MonitorError("--file は --target と一緒に使ってください（対象1つに限定）")

    # 黙って動いて通知が届かない状態を避けるため、起動直後に設定を確認する。
    # --file だけのパース確認では通知しないので対象外（--test-notify は確認する）。
    if not nt.topic_configured():
        if args.file and not args.test_notify:
            log("警告: NTFY_TOPIC が未設定です（--file のパース確認のため続行します）")
        else:
            raise MonitorError(
                "NTFY_TOPIC が設定されていません。"
                "通知先が無いまま監視しても空きに気づけないため終了します"
            )

    store = StateStore(args.state)
    now = now_jst()
    failures = []

    for target in targets:
        log(f"■ {target.name} [{target.id}] {target.date or ''}".rstrip())
        try:
            check_target(target, args, store, now)
        except MonitorError as exc:
            # 1つ壊れても他の対象の確認は続ける。失敗した対象の state は書かない。
            log(f"  エラー: {exc}")
            log("  この対象の state は更新していません")
            failures.append(target.id)

    if failures:
        log(f"失敗した対象: {', '.join(failures)}")
        return 1
    return 0


def main():
    ap = argparse.ArgumentParser(description="空き状況の監視")
    ap.add_argument("--targets", default="targets.yml", help="監視対象ファイル")
    ap.add_argument("--target", help="この id の対象だけを確認する")
    ap.add_argument("--list", action="store_true", help="対象の一覧を表示して終了")
    ap.add_argument("--state", default="state.json", help="状態ファイル")
    ap.add_argument("--file", help="実サイトの代わりにローカルHTMLを読む（テスト用）")
    ap.add_argument("--test-notify", action="store_true", help="通知を強制発火する")
    ap.add_argument(
        "--write-state-from-file", action="store_true",
        help="--file でも state を更新する（テスト用）",
    )
    args = ap.parse_args()

    try:
        return run(args)
    except MonitorError as exc:
        log(f"エラー: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
