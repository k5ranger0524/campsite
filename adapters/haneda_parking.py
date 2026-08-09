"""羽田空港 第2・第3駐車場 予約サービス（hnd-rsv.aeif.or.jp）用アダプタ。

トップページのカレンダーは空の <table> で、中身は JSON API から入る。
そのAPIを直接叩くのでブラウザ（Playwright）は不要。

    POST /airport2/app/calendar
    {"date": "", "area": "0", "handicapped": "0"}
    → {"date":"2026/08", "yoyakuCalendar":[{"date":"2026/08/22","day":"22","status":"full"}, ...]}

    area        0 = 第2駐車場(P2 / 第1ターミナル)、1 = 第3駐車場(P3 / 第2ターミナル)
    handicapped 0 = 一般者枠、1 = 身障者専用
    date        "" で当月。"YYYY/MM" で月指定（当月以外が要るときだけ使う）

status の実測値（2026-08-04 取得のキャプチャで確認。描画後の td class と一致）:
    "full"      満車
    "konzatsu"  混雑
    ""          期間外（過去日と、受付開始前＝30日より先）

**「空車」の値だけは未確認**。取得時点で空車の日が1日も無かったため。
凡例は 空車/混雑/満車/期間外 の4つなので、上記3つ以外は空車と解釈する
（unknown_status で変更可）。ここを「判定不能＝失敗」にすると、
空きが出たまさにその時に通知できなくなるため、空車寄りに倒している。

config:
    areas:
        P2: {id: 0, name: 第2駐車場}
        P3: {id: 1, name: 第3駐車場}
    dates: [2026-08-22, 2026-08-23, 2026-08-24, 2026-08-25]
    handicapped: 0
    status_map: {konzatsu: available}    # 既定を上書きしたいときだけ
    unknown_status: available
"""

import json
import re
from datetime import datetime

import requests

from core import status as st
from core.fetch import DEFAULT_HEADERS
from core.util import MonitorError, log

TOP_URL = "https://hnd-rsv.aeif.or.jp/airport2/app/toppage"
CALENDAR_URL = "https://hnd-rsv.aeif.or.jp/airport2/app/calendar"

# 実測で確認した対応
DEFAULT_STATUS_MAP = {
    "full": st.FULL,
    "konzatsu": st.AVAILABLE,      # 混雑＝予約は取れる。通知対象に含める
    "": st.CLOSED,                 # 期間外
}
DEFAULT_UNKNOWN = st.AVAILABLE     # 上記以外＝空車とみなす（未検証のため警告を出す）


def build_url(config, date_str):
    """通知に載せるURL。人が開いて予約できるページ。"""
    return TOP_URL


def _areas(config):
    areas = config.get("areas")
    if not areas:
        raise MonitorError("config に areas がありません")
    out = {}
    for key, spec in areas.items():
        if isinstance(spec, int):
            spec = {"id": spec}
        if "id" not in spec:
            raise MonitorError(f"areas.{key} に id がありません")
        out[key] = {"id": int(spec["id"]), "name": spec.get("name") or key}
    return out


def _dates(config):
    raw = config.get("dates")
    if not raw:
        raise MonitorError("config に dates がありません（例: [2026-08-22]）")
    if not isinstance(raw, list):
        raw = [raw]
    out = []
    for d in raw:
        try:
            out.append(datetime.strptime(str(d), "%Y-%m-%d").date())
        except ValueError:
            raise MonitorError(f"dates の形式が不正です: {d!r}（YYYY-MM-DD）")
    return sorted(out)


# --------------------------------------------------------------------------
def _post_calendar(session, month, area_id, handicapped, csrf):
    headers = dict(DEFAULT_HEADERS)
    headers.update({
        "Content-Type": "application/json",
        "X-Requested-With": "XMLHttpRequest",
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Referer": TOP_URL,
        "Origin": "https://hnd-rsv.aeif.or.jp",
    })
    if csrf:
        headers["X-CSRF-TOKEN"] = csrf

    payload = {"date": month, "area": str(area_id), "handicapped": str(handicapped)}
    try:
        resp = session.post(CALENDAR_URL, json=payload, headers=headers, timeout=30)
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException as exc:
        raise MonitorError(f"カレンダーAPIの取得に失敗しました (area={area_id}): {exc}")
    except ValueError as exc:
        raise MonitorError(f"カレンダーAPIの応答がJSONではありません (area={area_id}): {exc}")


def fetch(config, date_str):
    """必要な月ぶんのカレンダーを取得し、parse が読める形のJSON文字列で返す。"""
    areas = _areas(config)
    dates = _dates(config)
    handicapped = int(config.get("handicapped", 0))
    months = sorted({f"{d:%Y/%m}" for d in dates})

    session = requests.Session()
    try:
        top = session.get(TOP_URL, headers=DEFAULT_HEADERS, timeout=30)
        top.raise_for_status()
    except requests.RequestException as exc:
        raise MonitorError(f"トップページを取得できませんでした: {exc}")

    # Spring系のCSRF対策があっても通るようにトークンを拾っておく
    m = re.search(r'name="_csrf"\s+value="([^"]+)"', top.text) or \
        re.search(r'name="_csrf"\s+content="([^"]+)"', top.text)
    csrf = m.group(1) if m else None

    calendars = []
    for key, area in areas.items():
        for month in months:
            # "" は当月。実測で確認済みなのはこちらなので、当月は "" を使う
            body = _post_calendar(session, "", area["id"], handicapped, csrf)
            got = str(body.get("date") or "")
            if got != month:
                body = _post_calendar(session, month, area["id"], handicapped, csrf)
                got = str(body.get("date") or "")
                if got != month:
                    raise MonitorError(
                        f"{key}: {month} のカレンダーを取得できません（返ってきたのは {got!r}）"
                    )
            calendars.append({"area": key, "month": month, "body": body})

    return json.dumps({"calendars": calendars}, ensure_ascii=False)


# --------------------------------------------------------------------------
def _calendars_from(data):
    """fetch() の出力、またはキャプチャした api.json のどちらでも読めるようにする。"""
    if isinstance(data, dict) and "calendars" in data:
        return [(c["area"], c["body"]) for c in data["calendars"]]

    # capture.py が保存した形式: [{"post_data": ..., "body": "..."}, ...]
    if isinstance(data, list):
        out = []
        for entry in data:
            try:
                post = json.loads(entry.get("post_data") or "{}")
                body = json.loads(entry.get("body") or "{}")
            except ValueError:
                continue
            if "yoyakuCalendar" not in body:
                continue
            out.append(((int(post.get("area", -1)), int(post.get("handicapped", 0))), body))
        return out

    raise MonitorError("カレンダーのデータを解釈できません")


def parse(text, config, date_str, warn=None):
    warn = warn or (lambda msg: None)
    areas = _areas(config)
    dates = _dates(config)
    handicapped = int(config.get("handicapped", 0))

    status_map = dict(DEFAULT_STATUS_MAP)
    status_map.update(config.get("status_map") or {})
    unknown = config.get("unknown_status", DEFAULT_UNKNOWN)
    if unknown not in st.ALL:
        raise MonitorError(f"unknown_status が不正です: {unknown!r}")

    try:
        data = json.loads(text)
    except ValueError as exc:
        raise MonitorError(f"カレンダーのJSONを読めません: {exc}")

    raw = _calendars_from(data)

    # area キー（P2 など）で引けるように、日付 -> status の表を作る
    by_area = {}
    for ident, body in raw:
        cal = body.get("yoyakuCalendar")
        if not isinstance(cal, list):
            raise MonitorError("応答に yoyakuCalendar がありません")
        table = {c.get("date"): c.get("status") for c in cal if c.get("date")}

        if isinstance(ident, tuple):                 # キャプチャ形式: (area_id, handicapped)
            area_id, hc = ident
            if hc != handicapped:
                continue
            for key, area in areas.items():
                if area["id"] == area_id:
                    by_area.setdefault(key, {}).update(table)
        else:                                        # fetch形式: area キーそのもの
            by_area.setdefault(ident, {}).update(table)

    result = {}
    for key, area in areas.items():
        table = by_area.get(key)
        if not table:
            raise MonitorError(f"{key}: カレンダーが取得できていません")

        for d in dates:
            ymd = f"{d:%Y/%m/%d}"
            if ymd not in table:
                raise MonitorError(
                    f"{key}: {ymd} がカレンダーに含まれていません"
                    f"（取得できたのは {min(table)} 〜 {max(table)}）"
                )
            raw_status = table[ymd] or ""
            if raw_status in status_map:
                status = status_map[raw_status]
            else:
                status = unknown
                warn(
                    f"{key} {ymd}: 未知の status {raw_status!r} を "
                    f"{st.label(status)} として扱いました（空車の可能性）"
                )

            result[f"{key} {d.month}/{d.day}"] = {
                "name": f"{area['name']} {d.month}/{d.day}",
                "status": status,
                "raw": raw_status,
                # 「同じ駐車場で何日空いたか」を数えるための括り
                "series": key,
                "series_name": area["name"],
                "date": f"{d:%Y-%m-%d}",
            }

    return result


def discover(html, config, date_str, warn=None):
    """この駐車場は area が2つだけなので、固定で返す。"""
    return [
        {"key": "P2", "name": "第2駐車場（P2 / 第1ターミナル） area=0", "status": st.UNKNOWN},
        {"key": "P3", "name": "第3駐車場（P3 / 第2ターミナル） area=1", "status": st.UNKNOWN},
    ]
