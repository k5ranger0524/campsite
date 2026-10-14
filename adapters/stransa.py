"""Apotool & Box（stransa.co.jp）の歯科Web予約用アダプタ。

    https://reservation.stransa.co.jp/{office}/reserve/select-time?web-menu-id={menu}&date={date}
    例: さいたま新都心おとなこども歯科・矯正歯科「歯並びについて相談したい(60分)」

画面はReactで描画していて、HTMLには何も入っていない。空き枠は裏のJSON APIにある
（captures/stransa-1031.api.json で確認）ので、それを直接叩く:

    1. POST https://reservation-backend.stransa.co.jp/web-reservation/new-patient/token
           {"office": {"encodedID": "<office>"}}  → {"token": "eyJ..."}（1時間有効）
    2. GET  .../new-patient/reservation-frames
           ?from-ap=false&search-type=TIMES&from-date=..&to-date=..&web-menu-id=<menu>
           Authorization: <token>   ※ "Bearer " は付けない（実測で値の長さが一致）
       → {"reservationFrames": [{"date": "2026-10-31", "frameCount": 1,
                                 "times": ["12:00"]}, ...],
          "restDates": ["2026-10-30", ...]}

       ・times は「この時刻から予約できる」開始時刻。画面の○と1対1で一致する
       ・空き枠が1つも無い日は reservationFrames に出てこない（画面では全部×）
       ・休診日は restDates に出る
       ・web-menu-id を付けないと所要時間の違う枠が返る。必ず付ける

画面と同じく目的日の前後3日（計7日）を問い合わせる。

config:
    office: 0452f03db193db904abb2c19aa4f4960   # URLの先頭のID
    web_menu_id: 37430                          # URLの web-menu-id
    after: "14:00"                              # この時刻以降の開始枠だけを見る（任意）
    before: "17:00"                             # この時刻より前の開始枠だけを見る（任意）
"""

import json
import re
from datetime import datetime, timedelta
from urllib.parse import parse_qs, urlparse

import requests

from core import status as st
from core.fetch import DEFAULT_HEADERS
from core.util import MonitorError

PAGE_URL = ("https://reservation.stransa.co.jp/{office}/reserve/select-time"
            "?web-menu-id={menu}&date={date}")
API_BASE = "https://reservation-backend.stransa.co.jp/web-reservation/new-patient"

TIME_RE = re.compile(r"^\d{2}:\d{2}$")


def _office(config):
    office = config.get("office")
    if not office:
        raise MonitorError("config に office がありません（URLの先頭のID）")
    return str(office)


def _menu(config):
    menu = config.get("web_menu_id")
    if menu in (None, ""):
        raise MonitorError("config に web_menu_id がありません（URLの web-menu-id）")
    return str(menu)


def _target_date(date_str):
    if not date_str:
        raise MonitorError("このアダプタは date が必須です（例: 2026-10-24）")
    try:
        return datetime.strptime(str(date_str), "%Y-%m-%d").date()
    except ValueError:
        raise MonitorError(f"date の形式が不正です: {date_str!r}（YYYY-MM-DD）")


def _hhmm(config, key):
    value = config.get(key)
    if value in (None, ""):
        return None
    value = str(value)
    if not TIME_RE.match(value):
        raise MonitorError(f"{key} の形式が不正です: {value!r}（HH:MM）")
    return value


def _window_label(after, before):
    if after and before:
        return f"{after}〜{before}開始"
    if after:
        return f"{after}以降"
    if before:
        return f"{before}より前"
    return "全時間帯"


def build_url(config, date_str):
    return PAGE_URL.format(office=_office(config), menu=_menu(config),
                           date=_target_date(date_str))


# --------------------------------------------------------------------------
def _api_headers(token=None):
    headers = dict(DEFAULT_HEADERS)
    headers.update({
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "ja-JP",
        "Referer": "https://reservation.stransa.co.jp/",
        "Origin": "https://reservation.stransa.co.jp",
    })
    headers.pop("Upgrade-Insecure-Requests", None)
    if token:
        headers["Authorization"] = token
    return headers


def fetch(config, date_str):
    """トークンを取って空き枠APIを叩き、応答のJSON文字列をそのまま返す。"""
    target = _target_date(date_str)
    session = requests.Session()

    try:
        resp = session.post(f"{API_BASE}/token",
                            json={"office": {"encodedID": _office(config)}},
                            headers=_api_headers(), timeout=30)
        resp.raise_for_status()
        token = resp.json().get("token")
    except requests.RequestException as exc:
        raise MonitorError(f"トークンを取得できませんでした: {exc}")
    except ValueError as exc:
        raise MonitorError(f"トークンの応答がJSONではありません: {exc}")
    if not token:
        raise MonitorError("トークンの応答に token がありません")

    params = {
        "from-ap": "false",
        "search-type": "TIMES",
        "from-date": str(target - timedelta(days=3)),
        "to-date": str(target + timedelta(days=3)),
        "web-menu-id": _menu(config),
    }
    try:
        resp = session.get(f"{API_BASE}/reservation-frames", params=params,
                           headers=_api_headers(token), timeout=30)
        resp.raise_for_status()
    except requests.RequestException as exc:
        raise MonitorError(f"空き枠APIの取得に失敗しました: {exc}")
    try:
        body = resp.json()
    except ValueError as exc:
        raise MonitorError(f"空き枠APIの応答がJSONではありません: {exc}")
    # 問い合わせた期間も残す。期間外の日を「枠なし＝空きなし」と誤読しないため
    return json.dumps({"from": params["from-date"], "to": params["to-date"],
                       "body": body}, ensure_ascii=False)


# --------------------------------------------------------------------------
def _frames_body(data):
    """(from, to, 応答) を返す。

    fetch() の出力、またはキャプチャした api.json のどちらでも読めるようにする。
    """
    if isinstance(data, dict) and "body" in data:
        return data.get("from"), data.get("to"), data["body"]
    if isinstance(data, list):
        # capture.py の形式。web-menu-id 付きの最後の応答を使う
        for entry in reversed(data):
            url = entry.get("url") or ""
            if "reservation-frames" in url and "web-menu-id=" in url:
                query = parse_qs(urlparse(url).query)
                try:
                    body = json.loads(entry.get("body") or "")
                except ValueError:
                    break
                return (query.get("from-date", [None])[0],
                        query.get("to-date", [None])[0], body)
    raise MonitorError("空き枠のデータを解釈できません")


def parse(text, config, date_str, warn=None):
    warn = warn or (lambda msg: None)
    target = str(_target_date(date_str))
    after = _hhmm(config, "after")
    before = _hhmm(config, "before")

    try:
        data = json.loads(text)
    except ValueError as exc:
        raise MonitorError(f"空き枠のJSONを読めません: {exc}")
    date_from, date_to, body = _frames_body(data)
    if not (date_from and date_to and date_from <= target <= date_to):
        raise MonitorError(
            f"{target} は問い合わせた期間 {date_from}〜{date_to} に入っていません")

    frames = body.get("reservationFrames")
    rest = body.get("restDates")
    if not isinstance(frames, list) or not isinstance(rest, list):
        raise MonitorError(
            f"応答に reservationFrames / restDates がありません: {str(body)[:200]!r}")

    times = None
    for frame in frames:
        if frame.get("date") == target:
            times = frame.get("times")
            if not isinstance(times, list) or not all(
                    isinstance(t, str) and TIME_RE.match(t) for t in times):
                raise MonitorError(f"{target} の times を解釈できません: {times!r}")
            break

    label = _window_label(after, before)
    key = label
    if target in rest:
        status, times = st.CLOSED, []
    else:
        times = times or []          # 枠が1つも無い日は一覧に出てこない
        hits = [t for t in times
                if (after is None or t >= after) and (before is None or t < before)]
        status = st.AVAILABLE if hits else st.FULL
        if hits:
            label += "（" + "・".join(hits) + "）"

    return {key: {"name": f"{target} {label}", "status": status,
                  "raw": ",".join(times) or "-"}}
