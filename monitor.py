#!/usr/bin/env python3
"""赤城山オートキャンプ場 3家族サイト(F1〜F4) の空き状況を監視する。

役割分担:
    ntfy通知         = 空きが出た（本来の目的）        → exit 0
    ジョブ失敗(exit 1) = 監視が壊れた（異常検知）

空きあり状態が続いている間は REPEAT_HOURS ごとに再通知する。
満室に戻ったら通知履歴をリセットする。

使い方:
    python3 monitor.py                       # 実サイトを監視
    python3 monitor.py --file debug.html     # ローカルHTMLでパースをテスト
    python3 monitor.py --test-notify         # 通知処理だけを強制発火して確認

終了コード:
    0  正常終了（変化なし、または通知の送信に成功）
    1  異常終了（設定不備・取得失敗・構造変化・通知送信失敗）。state.json は更新しない
"""

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone

import requests
from bs4 import BeautifulSoup

# --------------------------------------------------------------------------
# 確定済みのHTML構造（調査フェーズで実データから確認済み）
# --------------------------------------------------------------------------
BASE_URL = "https://reserve.489ban.net/client/autocamp-akagi/0/plan/availability/room/stay"

# 監視対象: li[id^="room_"] の id
ROOMS = {
    "F1": "room_24246",
    "F2": "room_24247",
    "F3": "room_24248",
    "F4": "room_24249",
}

# td 内の <i> の class → 状態
ICON_STATUS = {
    "fa-circle": "available",       # 空きあり
    "fa-xmark": "full",             # 空きなし
    "fa-square-phone": "phone",     # 電話問い合わせ
    "fa-minus": "closed",           # 受付できません
}

STATUS_LABEL = {
    "available": "空きあり",
    "full": "空きなし",
    "phone": "電話問い合わせ",
    "closed": "受付できません",
}

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

JST = timezone(timedelta(hours=9))
DEFAULT_REPEAT_HOURS = 2.0


class MonitorError(Exception):
    """state.json を更新してはいけない異常。exit 1 で終了する。"""


def log(msg):
    print(f"[{datetime.now(JST):%Y-%m-%d %H:%M:%S}] {msg}", flush=True)


# --------------------------------------------------------------------------
# 取得
# --------------------------------------------------------------------------
def build_url(date_str):
    return f"{BASE_URL}?date={date_str}"


def fetch_html(url):
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": (
            "text/html,application/xhtml+xml,application/xml;q=0.9,"
            "image/avif,image/webp,*/*;q=0.8"
        ),
        "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
        "Upgrade-Insecure-Requests": "1",
    }
    delay = 2
    last = None
    for attempt in range(4):
        try:
            resp = requests.get(url, headers=headers, timeout=30)
            resp.raise_for_status()
            if "charset=" not in resp.headers.get("Content-Type", "").lower():
                resp.encoding = resp.apparent_encoding or "utf-8"
            return resp.text
        except requests.RequestException as exc:
            last = exc
            if attempt < 3:
                log(f"取得失敗 ({exc.__class__.__name__}): {delay}秒後に再試行")
                time.sleep(delay)
                delay *= 2
    raise MonitorError(f"ページ取得に失敗しました: {last}")


# --------------------------------------------------------------------------
# パース
# --------------------------------------------------------------------------
def date_label(date_str):
    """'2026-09-19' → '9/19'（th の span テキストと同じ形式）"""
    d = datetime.strptime(date_str, "%Y-%m-%d")
    return f"{d.month}/{d.day}"


def normalize_label(text):
    """'09/19' や ' 9 / 19 ' を '9/19' に揃える。"""
    m = re.search(r"(\d{1,2})\s*/\s*(\d{1,2})", text or "")
    if not m:
        return None
    return f"{int(m.group(1))}/{int(m.group(2))}"


def status_from_cell(td):
    """td 内の <i> の class から状態を決める。"""
    for i_tag in td.find_all("i"):
        for cls in i_tag.get("class") or []:
            if cls in ICON_STATUS:
                return ICON_STATUS[cls], cls
    return None, None


def find_room_state(li, want_label):
    """1サイト分の li から、目的の日付の状態を取り出す。

    日付列はインデックス決め打ちにせず、th の span テキストが一致する列を探す。
    """
    name_el = li.select_one("dl.webc_avlbl_item dt")
    name = name_el.get_text(strip=True) if name_el else None

    table = li.select_one("div.webc_avlbl_cal table")
    if table is None:
        raise MonitorError(f"{li.get('id')}: div.webc_avlbl_cal table が見つかりません")

    ths = table.select("thead th")
    tds = table.select("tbody td")
    if not ths or not tds:
        raise MonitorError(f"{li.get('id')}: thead th / tbody td が空です")
    if len(ths) != len(tds):
        raise MonitorError(
            f"{li.get('id')}: th({len(ths)})とtd({len(tds)})の数が一致しません。構造が変わった可能性があります"
        )

    # th の span[0] が日付ラベル
    col = None
    seen = []
    for idx, th in enumerate(ths):
        spans = th.find_all("span")
        if not spans:
            continue
        label = normalize_label(spans[0].get_text(strip=True))
        if label:
            seen.append(label)
        if label == want_label:
            col = idx
            break

    if col is None:
        raise MonitorError(
            f"{li.get('id')}: 日付 {want_label} の列が見つかりません（表示中: {seen}）"
        )

    td = tds[col]
    status, icon_cls = status_from_cell(td)
    if status is None:
        raise MonitorError(
            f"{li.get('id')}: {want_label} の td に既知のアイコンclassがありません: "
            f"{str(td)[:200]}"
        )

    link = td.find("a")
    href = link.get("href") if link else None

    return {
        "name": name,
        "status": status,
        "icon": icon_cls,
        "has_link": href is not None,
        "href": href,
        "column": col,
    }


def parse(html, date_str):
    """F1〜F4 すべての状態を返す。1つでも欠けたら MonitorError。"""
    soup = BeautifulSoup(html, "lxml")
    want = date_label(date_str)

    result = {}
    missing = []
    for key, room_id in ROOMS.items():
        li = soup.find("li", id=room_id)
        if li is None:
            missing.append(f"{key}({room_id})")
            continue
        result[key] = find_room_state(li, want)

    if missing:
        raise MonitorError(
            "監視対象サイトが見つかりません: " + ", ".join(missing)
            + "。サイト側のHTML構造が変わった可能性があります"
        )

    # 補助的な二重チェック: 空きありの td のみリンクを持つ
    for key, info in result.items():
        if info["status"] == "available" and not info["has_link"]:
            log(f"警告: {key} は空きあり判定だがリンクがありません（構造変化の可能性）")
        if info["status"] == "full" and info["has_link"]:
            log(f"警告: {key} は空きなし判定だがリンクがあります（構造変化の可能性）")

    return result


# --------------------------------------------------------------------------
# 状態の保存と比較
# --------------------------------------------------------------------------
def load_state(path):
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data.get("rooms", {})
    except (json.JSONDecodeError, OSError) as exc:
        log(f"警告: {path} を読めませんでした ({exc})。初回扱いにします")
        return {}


def save_state(path, rooms, previous, notified_keys, now, date_str):
    """正常にパースできた時だけ呼ばれる。

    notified_at は「空きあり中の最終通知時刻」。
    満室等に戻ったサイトは None にして通知履歴をリセットする。
    """
    out = {}
    for key, info in rooms.items():
        if info["status"] != "available":
            notified_at = None          # 満室に戻ったらリセット
        elif key in notified_keys:
            notified_at = now.isoformat()
        else:
            notified_at = (previous.get(key) or {}).get("notified_at")
        out[key] = {
            "status": info["status"],
            "name": info["name"],
            "notified_at": notified_at,
        }

    payload = {
        "updated_at": now.isoformat(),
        "target_date": date_str,
        "rooms": out,
    }
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, path)


def parse_dt(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def decide_notifications(current, previous, now, repeat_hours):
    """通知すべきサイトを (新規, 継続中) に分けて返す。

    新規  : 空きあり以外 → 空きあり に変化した
    継続中: 空きありが続いていて、最終通知から repeat_hours 以上経過した
    """
    new_keys, cont_keys = [], []
    interval = timedelta(hours=repeat_hours)

    for key in ROOMS:
        if current[key]["status"] != "available":
            continue
        prev = previous.get(key) or {}
        last = parse_dt(prev.get("notified_at"))

        if prev.get("status") != "available" or last is None:
            new_keys.append(key)
        elif now - last >= interval:
            cont_keys.append(key)
        else:
            remain = interval - (now - last)
            mins = int(remain.total_seconds() // 60)
            log(f"  {key}: 空きあり継続中だが前回通知から {mins} 分後まで再通知しません")

    return new_keys, cont_keys


# --------------------------------------------------------------------------
# 通知
# --------------------------------------------------------------------------
def build_message(new_keys, cont_keys, rooms, url, date_str, repeat_hours):
    def name_of(key):
        return (rooms.get(key) or {}).get("name") or key

    lines = []
    if new_keys:
        lines.append(f"{date_str} に空きが出ました。")
        lines.append("")
        for key in new_keys:
            lines.append(f"・{key}  {name_of(key)}")
    if cont_keys:
        if new_keys:
            lines.append("")
        lines.append(f"【継続中】{date_str} は引き続き空きがあります。")
        lines.append("")
        for key in cont_keys:
            lines.append(f"・{key}  {name_of(key)}（継続中）")
        lines.append("")
        lines.append(f"※ 空きが続く間は {repeat_hours:g} 時間ごとにお知らせします。")

    lines += ["", "予約ページ:", url]
    return "\n".join(lines)


def build_title(new_keys, cont_keys):
    if new_keys:
        return "赤城山オートキャンプ場 空き通知"
    return "赤城山オートキャンプ場 空き継続中"


def send_ntfy(topic, title, message, url):
    """ntfy.sh の JSON 送信エンドポイントを使う（ヘッダ経由だと日本語が壊れるため）。"""
    payload = {
        "topic": topic,
        "title": title,
        "message": message,
        "priority": 5,
        "tags": ["tent", "bell"],
        "click": url,
    }
    resp = requests.post(
        "https://ntfy.sh/", json=payload, timeout=30,
        headers={"User-Agent": USER_AGENT},
    )
    resp.raise_for_status()


def notify(new_keys, cont_keys, rooms, url, date_str, repeat_hours):
    """通知を送る。送信できなければ MonitorError（＝state を更新させない）。"""
    title = build_title(new_keys, cont_keys)
    message = build_message(new_keys, cont_keys, rooms, url, date_str, repeat_hours)

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


# --------------------------------------------------------------------------
def run(args):
    url = build_url(args.date)
    now = datetime.now(JST)

    # 黙って動いて通知が届かない状態を避けるため、起動直後に設定を確認する。
    # --file だけのパース確認では通知しないので対象外（--test-notify は確認する）。
    if not os.environ.get("NTFY_TOPIC", "").strip():
        if args.file and not args.test_notify:
            log("警告: NTFY_TOPIC が未設定です（--file のパース確認のため続行します）")
        else:
            raise MonitorError(
                "NTFY_TOPIC が設定されていません。"
                "通知先が無いまま監視しても空きに気づけないため終了します"
            )

    if args.file:
        log(f"ローカルファイルを読み込みます: {args.file}")
        try:
            with open(args.file, encoding="utf-8") as f:
                html = f.read()
        except OSError as exc:
            raise MonitorError(f"ファイルを読めません: {exc}")
    else:
        log(f"取得します: {url}")
        html = fetch_html(url)

    rooms = parse(html, args.date)

    log(f"監視対象 {args.date} の判定結果:")
    for key in ROOMS:
        info = rooms[key]
        log(
            f"  {key}  {STATUS_LABEL[info['status']]:8s} "
            f"(icon={info['icon']}, 列={info['column']}, リンク={'有' if info['has_link'] else '無'})  {info['name']}"
        )

    if args.test_notify:
        log("--test-notify: 状態に関わらず通知を強制発火します（state.json は更新しません）")
        notify(list(ROOMS), [], rooms, url, args.date, args.repeat_hours)
        return 0

    previous = load_state(args.state)
    if not previous:
        log("前回の状態がありません（初回実行）")

    new_keys, cont_keys = decide_notifications(rooms, previous, now, args.repeat_hours)

    if new_keys or cont_keys:
        if new_keys:
            log(f"空きを検知: {', '.join(new_keys)}")
        if cont_keys:
            log(f"空きあり継続中（再通知）: {', '.join(cont_keys)}")
        notify(new_keys, cont_keys, rooms, url, args.date, args.repeat_hours)
    else:
        log("通知対象はありません")

    # ここまで来た＝取得もパースも通知も成功。状態を確定させる。
    if args.file and not args.write_state_from_file:
        log("--file モードのため state.json は更新しません（--write-state-from-file で上書き可）")
    else:
        save_state(args.state, rooms, previous, new_keys + cont_keys, now, args.date)
        log(f"状態を保存しました: {args.state}")

    return 0


def main():
    ap = argparse.ArgumentParser(description="赤城山オートキャンプ場 空き監視")
    ap.add_argument("--date", default="2026-09-19", help="監視対象日 (YYYY-MM-DD)")
    ap.add_argument("--file", help="実サイトの代わりにローカルHTMLを読む（テスト用）")
    ap.add_argument("--state", default="state.json", help="状態ファイルのパス")
    ap.add_argument("--test-notify", action="store_true", help="通知処理を強制発火する")
    ap.add_argument(
        "--repeat-hours", type=float, default=DEFAULT_REPEAT_HOURS,
        help=f"空きあり継続中の再通知間隔（時間、既定 {DEFAULT_REPEAT_HOURS:g}）",
    )
    ap.add_argument(
        "--write-state-from-file", action="store_true",
        help="--file モードでも state.json を更新する（テスト用）",
    )
    args = ap.parse_args()

    try:
        return run(args)
    except MonitorError as exc:
        log(f"エラー: {exc}")
        log("state.json は更新していません")
        return 1


if __name__ == "__main__":
    sys.exit(main())
