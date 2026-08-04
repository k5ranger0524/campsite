"""489ban.net（予約番）系の空き状況ページ用アダプタ。

赤城山オートキャンプ場で実データから確認した構造:

    li[id="room_24246"]                     … 1区画/1部屋のコンテナ
      dl.webc_avlbl_item dt                 … 名称
      div.webc_avlbl_cal table
        thead th  … 14個。span[0]が日付、span[1]が曜日
                    月付き表記("9/19")は先頭列だけ。以降は日のみ("20")
        tbody td  … 14個。thead と同じ並び
          i.fa-circle / fa-xmark / ...      … 空き状況
        空きありの td だけ <a> を持つ

同じ 489ban 上の別施設は facility を差し替えるだけで動く想定
（未検証。discover.py で1回叩けば確認できる）。

config:
    facility: autocamp-akagi        # URLの /client/<ここ>/
    items:                          # 監視したい対象。キーは通知に出る短い名前
        F1: room_24246
"""

import re
from datetime import datetime

from bs4 import BeautifulSoup

from core import status as st
from core.util import MonitorError

BASE_URL = "https://reserve.489ban.net/client/{facility}/0/plan/availability/room/stay"

# td 内の <i> の class → 共通語彙
ICON_STATUS = {
    "fa-circle": st.AVAILABLE,
    "fa-xmark": st.FULL,
    "fa-square-phone": st.PHONE,
    "fa-minus": st.CLOSED,
}

WEEK = "月火水木金土日"


# --------------------------------------------------------------------------
def _facility(config):
    facility = config.get("facility")
    if not facility:
        raise MonitorError("config に facility がありません")
    return facility


def _as_date(date_str):
    if not date_str:
        raise MonitorError("このアダプタは date が必須です（例: 2026-09-19）")
    try:
        return datetime.strptime(str(date_str), "%Y-%m-%d").date()
    except ValueError:
        raise MonitorError(f"date の形式が不正です: {date_str!r}（YYYY-MM-DD）")


def build_url(config, date_str):
    return f"{BASE_URL.format(facility=_facility(config))}?date={date_str}"


# --------------------------------------------------------------------------
def parse_header_label(text):
    """th の span[0] を解釈する。先頭列 "9/19" → ("md",9,19) / 以降 "20" → ("d",None,20)"""
    text = (text or "").strip()
    m = re.search(r"(\d{1,2})\s*/\s*(\d{1,2})", text)
    if m:
        return "md", int(m.group(1)), int(m.group(2))
    m = re.fullmatch(r"\s*(\d{1,2})\s*", text)
    if m:
        return "d", None, int(m.group(1))
    return None


def infer_base_date(month, day, target):
    """先頭列の 'M/D' に年を補う。年は表記に無いので target に最も近い年を選ぶ。"""
    candidates = []
    for year in (target.year - 1, target.year, target.year + 1):
        try:
            candidates.append(datetime(year, month, day).date())
        except ValueError:
            continue        # 2/29 など
    if not candidates:
        return None
    return min(candidates, key=lambda d: abs((d - target).days))


def find_date_column(ths, target, where, warn):
    """目的の日付が何列目かを求める。

    月付き表記は先頭列にしか無いため、ラベルの総当たり一致では列0しか引けない。
    先頭列を基準日として「何日後か」で列を決め、その列が本当に目的の日か検証する。
    """
    spans = ths[0].find_all("span")
    if not spans:
        raise MonitorError(f"{where}: 先頭列に span がありません")
    head = parse_header_label(spans[0].get_text(strip=True))
    if not head or head[0] != "md":
        raise MonitorError(
            f"{where}: 先頭列から基準日を読めません "
            f"(span[0]={spans[0].get_text(strip=True)!r} / 月付き表記を期待)"
        )

    base = infer_base_date(head[1], head[2], target)
    if base is None:
        raise MonitorError(f"{where}: 先頭列の日付 {head[1]}/{head[2]} を解釈できません")

    col = (target - base).days
    if not (0 <= col < len(ths)):
        raise MonitorError(
            f"{where}: {target.month}/{target.day} は表示期間外です "
            f"(表示は {base:%Y-%m-%d} から {len(ths)}日分)"
        )

    spans = ths[col].find_all("span")
    label = parse_header_label(spans[0].get_text(strip=True)) if spans else None
    if label is None:
        raise MonitorError(
            f"{where}: 列{col} の日付ラベルを読めません "
            f"(span={[s.get_text(strip=True) for s in spans]})"
        )
    if label[2] != target.day or (label[0] == "md" and label[1] != target.month):
        raise MonitorError(
            f"{where}: 列{col} は {target.month}/{target.day} のはずですが表示は "
            f"{spans[0].get_text(strip=True)!r} です。表の構造が変わった可能性があります"
        )

    if len(spans) > 1:
        got = spans[1].get_text(strip=True)
        want = WEEK[target.weekday()]
        if got and got != want:
            warn(f"{where}: 列{col} の曜日が {got!r}（{want!r} を期待）")

    return col


def status_from_cell(td):
    for i_tag in td.find_all("i"):
        for cls in i_tag.get("class") or []:
            if cls in ICON_STATUS:
                return ICON_STATUS[cls], cls
    return None, None


def read_item(li, target, warn):
    """1区画分の li から、目的の日付の状態を取り出す。"""
    where = li.get("id")
    name_el = li.select_one("dl.webc_avlbl_item dt")
    name = name_el.get_text(strip=True) if name_el else None

    table = li.select_one("div.webc_avlbl_cal table")
    if table is None:
        raise MonitorError(f"{where}: div.webc_avlbl_cal table が見つかりません")

    ths = table.select("thead th")
    tds = table.select("tbody td")
    if not ths or not tds:
        raise MonitorError(f"{where}: thead th / tbody td が空です")
    if len(ths) != len(tds):
        raise MonitorError(
            f"{where}: th({len(ths)})とtd({len(tds)})の数が一致しません。"
            f"構造が変わった可能性があります"
        )

    col = find_date_column(ths, target, where, warn)
    td = tds[col]
    status, icon = status_from_cell(td)
    if status is None:
        raise MonitorError(
            f"{where}: {target.month}/{target.day} の td に既知のアイコンclassが"
            f"ありません: {str(td)[:200]}"
        )

    href = td.find("a")
    return {
        "name": name,
        "status": status,
        "icon": icon,
        "has_link": href is not None,
        "column": col,
    }


# --------------------------------------------------------------------------
def parse(html, config, date_str, warn=None):
    warn = warn or (lambda msg: None)
    target = _as_date(date_str)
    items_cfg = config.get("items") or {}
    if not items_cfg:
        raise MonitorError("config に items がありません")

    soup = BeautifulSoup(html, "lxml")

    result, missing = {}, []
    for key, element_id in items_cfg.items():
        li = soup.find("li", id=element_id)
        if li is None:
            missing.append(f"{key}({element_id})")
            continue
        result[key] = read_item(li, target, warn)

    if missing:
        raise MonitorError(
            "監視対象が見つかりません: " + ", ".join(missing)
            + "。サイト側のHTML構造が変わった可能性があります"
        )

    # 補助的な二重チェック: 空きありの td のみリンクを持つ
    for key, info in result.items():
        if info["status"] == st.AVAILABLE and not info["has_link"]:
            warn(f"{key} は空きあり判定だがリンクがありません（構造変化の可能性）")
        if info["status"] == st.FULL and info["has_link"]:
            warn(f"{key} は空きなし判定だがリンクがあります（構造変化の可能性）")

    return result


def discover(html, config, date_str, warn=None):
    """設定に書く項目を洗い出すための一覧。施設内の全区画を返す。"""
    warn = warn or (lambda msg: None)
    target = _as_date(date_str)
    soup = BeautifulSoup(html, "lxml")

    found = []
    for li in soup.find_all("li", id=re.compile(r"^room_\d+$")):
        try:
            info = read_item(li, target, warn)
        except MonitorError as exc:
            found.append({"key": li.get("id"), "name": f"(読めません: {exc})",
                          "status": st.UNKNOWN})
            continue
        found.append({"key": li.get("id"), "name": info["name"],
                      "status": info["status"]})
    return found
