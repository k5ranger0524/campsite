"""羽田空港 P4 / P5 駐車場予約サービス用アダプタ。

別サイトだが中身の作りが同じなので1つで両方に対応する。
どちらもサーバ側で描画されているため、ブラウザ（Playwright）は不要。

    P4: https://haneda-p4.jp/airport/
    P5: https://pk-reserve.haneda-airport.jp/airport/entrance/0000.jsf

実データ（2026-08-05 取得のキャプチャ）で確認した構造:

    table.calendar_btm          … 見出し画像と「8月」の月表示
      img src=".../public_month.gif"    一般
      img src=".../private_month.gif"   個室
      img src=".../handicap_month.gif"  身障者（P5のみ）
    直後の table.calendar_waku(_body)  … その種別のカレンダー
      <td class="blue full"><span>22</span></td>

    セルのclass（凡例と一致することを確認済み）:
      empty        空車
      congestion   混雑
      full         満車
      unavailable  予約対象外 / 期間外
    red / blue は日曜・土曜の色分けなので判定には使わない。

カレンダーは当月のみ。前後の月の日も枠に入るので、日付は「最初に現れる 1 の位置」
を当月1日として数える（"1" や "26" は前後の月にも出るため、数字だけでは引けない）。

config:
    url: https://haneda-p4.jp/airport/
    kinds:
        一般: public
        個室: private
    dates: [2026-08-22, 2026-08-23, 2026-08-24, 2026-08-25]
    status_map: {congestion: available}   # 既定を上書きしたいときだけ
"""

import re
from datetime import datetime

from bs4 import BeautifulSoup

from core import status as st
from core.util import MonitorError

# 見出し画像のファイル名 → 種別キー
KIND_IMAGES = {
    "public_month": "public",
    "private_month": "private",
    "handicap_month": "handicap",
}

KIND_LABEL = {
    "public": "一般",
    "private": "個室",
    "handicap": "身障者",
}

# セルの class → 共通語彙（凡例と突き合わせて確認済み）
DEFAULT_STATUS_MAP = {
    "empty": st.AVAILABLE,
    "congestion": st.AVAILABLE,     # 混雑＝予約は取れる。通知対象に含める
    "full": st.FULL,
    "unavailable": st.CLOSED,       # 予約対象外 / 期間外
}

IGNORED_CLASSES = {"red", "blue", "week", "today"}


def build_url(config, date_str):
    url = config.get("url")
    if not url:
        raise MonitorError("config に url がありません")
    return url


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


def _kinds(config):
    kinds = config.get("kinds")
    if not kinds:
        raise MonitorError("config に kinds がありません（例: {一般: public}）")
    out = {}
    for label, kind in kinds.items():
        kind = str(kind)
        if kind not in KIND_LABEL:
            raise MonitorError(
                f"kinds.{label} の値が不正です: {kind!r}（{'/'.join(KIND_LABEL)}）"
            )
        out[str(label)] = kind
    return out


# --------------------------------------------------------------------------
def _collect_calendars(soup):
    """{種別: (月, カレンダーtable)} を返す。"""
    found = {}
    for head in soup.select("table.calendar_btm"):
        kind = None
        for img in head.find_all("img"):
            src = img.get("src") or ""
            for marker, k in KIND_IMAGES.items():
                if marker in src:
                    kind = k
                    break
            if kind:
                break
        if kind is None:
            continue

        m = re.search(r"(\d{1,2})\s*月", head.get_text(" ", strip=True))
        month = int(m.group(1)) if m else None

        table = head.find_next(
            lambda t: t.name == "table"
            and any(str(c).startswith("calendar_waku") for c in (t.get("class") or []))
        )
        if table is None:
            raise MonitorError(f"{KIND_LABEL[kind]}: カレンダー本体が見つかりません")

        found[kind] = (month, table)
    return found


def _day_cells(table):
    """曜日見出しを除いた日付セルを、並び順のまま返す。"""
    cells = []
    for td in table.find_all("td"):
        classes = set(td.get("class") or [])
        if "week" in classes:
            continue
        if not td.get_text(strip=True).isdigit():
            continue
        cells.append(td)
    return cells


def _cell_for_day(cells, day, where):
    """当月 day 日のセルを返す。

    前後の月の日も枠に入るため、最初の "1" を当月1日として数える。
    """
    first = None
    for i, td in enumerate(cells):
        if td.get_text(strip=True) == "1":
            first = i
            break
    if first is None:
        raise MonitorError(f"{where}: カレンダーに1日が見つかりません")

    idx = first + day - 1
    if idx >= len(cells):
        raise MonitorError(f"{where}: {day}日がカレンダーの範囲外です")

    td = cells[idx]
    got = td.get_text(strip=True)
    if got != str(day):
        raise MonitorError(
            f"{where}: {day}日のはずの位置に {got!r} があります。"
            f"カレンダーの構造が変わった可能性があります"
        )
    return td


def _status_of(td, status_map, where):
    candidates = [c for c in (td.get("class") or []) if c not in IGNORED_CLASSES]
    for c in candidates:
        if c in status_map:
            return status_map[c], c
    raise MonitorError(
        f"{where}: 空き状況を判定できません（class={candidates or '(なし)'}）。"
        f"サイトの表記が変わった可能性があります"
    )


# --------------------------------------------------------------------------
def parse(html, config, date_str, warn=None):
    warn = warn or (lambda msg: None)
    kinds = _kinds(config)
    dates = _dates(config)

    status_map = dict(DEFAULT_STATUS_MAP)
    status_map.update(config.get("status_map") or {})

    soup = BeautifulSoup(html, "lxml")
    calendars = _collect_calendars(soup)
    if not calendars:
        raise MonitorError(
            "カレンダーが1つも見つかりません。サイトの構造が変わった可能性があります"
        )

    result = {}
    for label, kind in kinds.items():
        if kind not in calendars:
            raise MonitorError(
                f"{label}({kind}) のカレンダーがページにありません"
                f"（見つかったのは {sorted(calendars)}）"
            )
        month, table = calendars[kind]
        cells = _day_cells(table)

        for d in dates:
            where = f"{label} {d.month}/{d.day}"
            if month is not None and month != d.month:
                raise MonitorError(
                    f"{where}: カレンダーは{month}月を表示しています。"
                    f"月をまたぐ日付の確認には対応していません"
                )
            td = _cell_for_day(cells, d.day, where)
            status, raw = _status_of(td, status_map, where)
            result[f"{label} {d.month}/{d.day}"] = {
                "name": f"{label} {d.month}/{d.day}",
                "status": status,
                "raw": raw,
            }

    return result


def discover(html, config, date_str, warn=None):
    """ページにあるカレンダーの種別を洗い出す。"""
    soup = BeautifulSoup(html, "lxml")
    out = []
    for kind, (month, _) in _collect_calendars(soup).items():
        out.append({
            "key": kind,
            "name": f"{KIND_LABEL[kind]}（{month}月を表示中）",
            "status": st.UNKNOWN,
        })
    return out
