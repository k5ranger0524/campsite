"""タイムズのB（btimes.jp）予約制駐車場用アダプタ。

    https://btimes.jp/{pref}/park/{park_id}/
    例: https://btimes.jp/tokyo/park/55937/  （変なホテル東京羽田駐車場）

サーバ側でHTMLに埋め込まれているので requests だけで取得できる。

HTML構造:

    tr#reserveStatusTr        … 1行=1日。14行（予約可能日数の上限）
      ※ id が14行すべてで重複しているので select() で全件取ること
      ※ 今日から14日先までのローリング表示。毎日1日ずつ進むので、
         行番号で目的日を特定してはいけない。必ず日付で引く

      tr の class                     full / vaca
      p#reserveStatus の class        l-main-status-full / l-main-status-vaca
        → badge側を主判定にし、tr側と食い違ったら警告を出す

    日付の在り処が行の状態によって違う:
      満車(full)の行:  input#dateDatetime の value = "2026-08-22"
                       （この行の time#use の datetime は空なので使えない）
      空き(vaca)の行:  time#date の datetime = "2026-08-18"
                       input#day3-hidden の value = "20260818"
      → 順に試して取れたものを使う。どれも取れなければ MonitorError

    span#useDate      "8/22（土）"  … ISO日付とのクロスチェックに使う
    span#startTime / span#endTime / span#price … 空きの行のみ。通知本文に載せる

config:
    pref: tokyo
    park_id: 55937
    name: 変なホテル東京羽田駐車場      # 任意。通知の表示名
    allow_out_of_range: true          # 目的日がまだ表示範囲外でもエラーにしない
"""

import re
from datetime import datetime

from bs4 import BeautifulSoup

from core import status as st
from core.util import MonitorError

BASE_URL = "https://btimes.jp/{pref}/park/{park_id}/"

# p#reserveStatus / tr の class → 共通語彙
STATUS_MAP = {
    "vaca": st.AVAILABLE,
    "full": st.FULL,
}

BADGE_PREFIX = "l-main-status-"
BADGE_IGNORED = {"badge"}


def _pref(config):
    pref = config.get("pref")
    if not pref:
        raise MonitorError("config に pref がありません（例: tokyo）")
    return str(pref)


def _park_id(config):
    park_id = config.get("park_id")
    if park_id in (None, ""):
        raise MonitorError("config に park_id がありません（例: 55937）")
    return str(park_id)


def _target_date(date_str):
    if not date_str:
        raise MonitorError("このアダプタは date が必須です（例: 2026-08-22）")
    try:
        return datetime.strptime(str(date_str), "%Y-%m-%d").date()
    except ValueError:
        raise MonitorError(f"date の形式が不正です: {date_str!r}（YYYY-MM-DD）")


def build_url(config, date_str):
    return BASE_URL.format(pref=_pref(config), park_id=_park_id(config))


# --------------------------------------------------------------------------
def _text(row, selector):
    el = row.select_one(selector)
    if el is None:
        return None
    value = el.get_text(strip=True)
    return value or None


def _row_date(row, index):
    """行のISO日付を返す。状態によって置き場所が違うので順に試す。"""
    el = row.select_one("input#dateDatetime")
    if el is not None:
        value = (el.get("value") or "").strip()
        if value:
            try:
                return datetime.strptime(value, "%Y-%m-%d").date()
            except ValueError:
                raise MonitorError(
                    f"{index}行目: input#dateDatetime の値を読めません: {value!r}"
                )

    el = row.select_one("time#date")
    if el is not None:
        value = (el.get("datetime") or "").strip()
        if value:
            try:
                return datetime.strptime(value, "%Y-%m-%d").date()
            except ValueError:
                raise MonitorError(
                    f"{index}行目: time#date の datetime を読めません: {value!r}"
                )

    el = row.select_one("input#day3-hidden")
    if el is not None:
        value = (el.get("value") or "").strip()
        if value:
            try:
                return datetime.strptime(value, "%Y%m%d").date()
            except ValueError:
                raise MonitorError(
                    f"{index}行目: input#day3-hidden の値を読めません: {value!r}"
                )

    raise MonitorError(
        f"{index}行目: 日付を取得できません"
        f"（input#dateDatetime / time#date / input#day3-hidden のいずれも空）。"
        f"サイトの構造が変わった可能性があります"
    )


def _verify_label(row, date, index):
    """span#useDate（"8/22（土）"）と ISO日付が食い違っていないか確かめる。"""
    label = _text(row, "span#useDate")
    if not label:
        return None
    m = re.search(r"(\d{1,2})\s*/\s*(\d{1,2})", label)
    if not m:
        return label
    month, day = int(m.group(1)), int(m.group(2))
    if (month, day) != (date.month, date.day):
        raise MonitorError(
            f"{index}行目: 日付が食い違っています"
            f"（属性は {date:%Y-%m-%d}、表示は {label!r}）。"
            f"サイトの構造が変わった可能性があります"
        )
    return label


def _row_status(row, index, warn):
    """p#reserveStatus の class を主判定にし、tr の class と突き合わせる。"""
    badge = row.select_one("p#reserveStatus")
    if badge is None:
        raise MonitorError(
            f"{index}行目: p#reserveStatus がありません。"
            f"サイトの構造が変わった可能性があります"
        )

    keys = [
        c[len(BADGE_PREFIX):] for c in (badge.get("class") or [])
        if c.startswith(BADGE_PREFIX) and c[len(BADGE_PREFIX):] not in BADGE_IGNORED
    ]
    known = [k for k in keys if k in STATUS_MAP]
    if not known:
        raise MonitorError(
            f"{index}行目: 空き状況を判定できません"
            f"（p#reserveStatus の class={badge.get('class')}）。"
            f"サイトの表記が変わった可能性があります"
        )
    raw = known[0]

    # 補助チェック: tr 側の class と一致するはず
    tr_keys = [c for c in (row.get("class") or []) if c in STATUS_MAP]
    if tr_keys and tr_keys[0] != raw:
        warn(f"{index}行目: tr の class({tr_keys[0]}) と "
             f"バッジ({raw}) が食い違っています（バッジを採用）")

    return STATUS_MAP[raw], raw


def _row_detail(row):
    """空きの行にある時間帯と料金。通知本文に載せる。"""
    start = _text(row, "span#startTime")
    end = _text(row, "span#endTime")
    price = _text(row, "span#price")

    parts = []
    if start and end:
        parts.append(f"{start}〜{end}")
    if price:
        parts.append(f"{price}円")
    return " ".join(parts)


# --------------------------------------------------------------------------
def _rows(html, warn):
    soup = BeautifulSoup(html, "lxml")
    # id が重複しているので find(id=...) では1件しか取れない
    rows = soup.select("tr#reserveStatusTr")
    if not rows:
        raise MonitorError(
            "tr#reserveStatusTr が1つも見つかりません。"
            "サイトの構造が変わった可能性があります"
        )

    out = []
    for index, row in enumerate(rows, start=1):
        date = _row_date(row, index)
        label = _verify_label(row, date, index)
        status, raw = _row_status(row, index, warn)
        out.append({
            "date": date,
            "label": label,
            "status": status,
            "raw": raw,
            "detail": _row_detail(row),
        })
    return out


def parse(html, config, date_str, warn=None):
    warn = warn or (lambda msg: None)
    target = _target_date(date_str)
    park_name = config.get("name") or f"btimes {_park_id(config)}"

    rows = _rows(html, warn)
    found = next((r for r in rows if r["date"] == target), None)

    key = f"{target.month}/{target.day}"

    if found is None:
        lo, hi = min(r["date"] for r in rows), max(r["date"] for r in rows)
        shown = f"{lo.month}/{lo.day}〜{hi.month}/{hi.day}"
        if config.get("allow_out_of_range"):
            # 予約可能日数（14日）より先の日付は、まだ表に出てこない。
            # 監視を早く始めただけなので異常ではない。
            warn(f"{key} はまだ表示範囲外です（表示中: {shown}）")
            return {
                key: {
                    "name": f"{park_name} {target.month}/{target.day}（表示範囲外）",
                    "status": st.CLOSED,
                    "raw": "out-of-range",
                    "series": park_name,
                    "series_name": park_name,
                    "date": f"{target:%Y-%m-%d}",
                }
            }
        raise MonitorError(
            f"{target:%Y-%m-%d} の行が見つかりません（表示中: {shown}）。"
            f"表示範囲外を許容するなら config に allow_out_of_range: true を入れてください"
        )

    name = f"{park_name} {found['label'] or key}"
    if found["detail"]:
        name = f"{name} {found['detail']}"

    return {
        key: {
            "name": name,
            "status": found["status"],
            "raw": found["raw"],
            "series": park_name,
            "series_name": park_name,
            "date": f"{target:%Y-%m-%d}",
        }
    }


def discover(html, config, date_str, warn=None):
    """表示されている14日ぶんを一覧にする。"""
    warn = warn or (lambda msg: None)
    out = []
    for row in _rows(html, warn):
        name = row["label"] or f"{row['date']:%Y-%m-%d}"
        if row["detail"]:
            name = f"{name} {row['detail']}"
        out.append({
            "key": f"{row['date']:%Y-%m-%d}",
            "name": name,
            "status": row["status"],
        })
    return out
