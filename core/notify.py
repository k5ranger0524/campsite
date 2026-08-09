"""通知の文面と送信。監視対象の種類（キャンプ場・駐車場・宿など）に依存しない。"""

import os

import requests

from . import status as st
from .fetch import USER_AGENT
from .util import MonitorError, log

NTFY_ENDPOINT = "https://ntfy.sh/"


def _name_of(items, key):
    return (items.get(key) or {}).get("name") or key


def _when(date_str):
    """日付を持たない対象（例: 今この瞬間の満空）もあるので、無ければ空文字。"""
    return f"{date_str} は" if date_str else ""


def _short_date(iso):
    try:
        y, m, d = str(iso).split("-")
        return f"{int(m)}/{int(d)}"
    except (ValueError, AttributeError):
        return str(iso)


def build_alert(target_name, new_keys, cont_keys, items, url, date_str, repeat_hours,
                min_available=1, priority_dates=()):
    lines = []
    if new_keys:
        head = f"{date_str} に空きが出ました。" if date_str else "空きが出ました。"
        lines += [head, ""]
        for key in new_keys:
            lines.append(f"・{key}  {_name_of(items, key)}")
    if cont_keys:
        if new_keys:
            lines.append("")
        lines += [f"【継続中】{_when(date_str)}引き続き空きがあります。", ""]
        for key in cont_keys:
            lines.append(f"・{key}  {_name_of(items, key)}（継続中）")
        lines += ["", f"※ 空きが続く間は {repeat_hours:g} 時間ごとにお知らせします。"]

    if min_available > 1:
        note = f"※ 同じ枠で {min_available} 日以上"
        if priority_dates:
            note += "、または " + "・".join(_short_date(d) for d in priority_dates)
        note += "空いたときにお知らせしています。"
        lines += ["", note]

    lines += ["", "予約ページ:", url]
    return "\n".join(lines)


def build_alert_title(target_name, new_keys):
    return f"{target_name} 空き通知" if new_keys else f"{target_name} 空き継続中"


def build_test_message(target_name, items, url, date_str):
    """テスト通知の本文。

    「空きが出ました」とは書かない。本物の空き通知と見分けられなくなるため。
    代わりに、その時点の実際の判定結果をそのまま載せる。
    """
    heading = f"現在の判定結果（{date_str}）:" if date_str else "現在の判定結果:"
    lines = [
        "これは通知テストです。空きが出たという意味ではありません。",
        "",
        f"対象: {target_name}",
        "",
        heading,
    ]
    for key, info in items.items():
        name = info.get("name") or ""
        lines.append(f"・{key}  {st.label(info['status'])}  {name}".rstrip())
    lines += ["", "予約ページ:", url]
    return "\n".join(lines)


def build_test_title(target_name):
    return f"【テスト】{target_name} 監視テスト"


def send_ntfy(topic, title, message, url):
    """ntfy.sh の JSON 送信エンドポイントを使う（ヘッダ経由だと日本語が壊れるため）。"""
    payload = {
        "topic": topic,
        "title": title,
        "message": message,
        "priority": 5,
        "tags": ["bell"],
        "click": url,
    }
    resp = requests.post(
        NTFY_ENDPOINT, json=payload, timeout=30,
        headers={"User-Agent": USER_AGENT},
    )
    resp.raise_for_status()


def topic_configured():
    return bool(os.environ.get("NTFY_TOPIC", "").strip())


def deliver(title, message, url):
    """通知を送る。送信できなければ MonitorError（＝state を更新させない）。"""
    log(f"―― 通知内容（{title}） ――")
    for line in message.splitlines():
        log("  " + line)
    log("――――――――――")

    topic = os.environ.get("NTFY_TOPIC", "").strip()
    if not topic:
        raise MonitorError(
            "NTFY_TOPIC が未設定のため通知を送れません。環境変数を設定してください"
        )

    try:
        send_ntfy(topic, title, message, url)
    except requests.RequestException as exc:
        raise MonitorError(f"ntfy.sh への送信に失敗しました: {exc}")

    log(f"ntfy.sh に送信しました (topic={topic})")
