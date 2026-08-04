"""新しい予約システムに対応するときの雛形。このファイルをコピーして使う。

先頭が _ なので、このままでは読み込まれない（adapters/<名前>.py にリネームすること）。

書くのはこの1ファイルだけでよい。取得のリトライ・状態の保存・再通知の間隔・
通知の文面・エラー時に state を書かない規律は、すべて共通側が面倒を見る。

作業の順序:
    1. 対象ページのHTMLを保存する    curl -A "Mozilla/5.0 ..." "URL" -o debug.html
    2. build_url() を書く
    3. parse() を書く（ここが本体）
    4. python3 monitor.py --target <id> --file debug.html で確認
"""

from bs4 import BeautifulSoup

from core import status as st
from core.util import MonitorError


def build_url(config, date_str):
    """取得先URL。通知本文にも載るので、人が開けるURLにすること。"""
    base = config.get("url")
    if not base:
        raise MonitorError("config に url がありません")
    return base.replace("{date}", str(date_str)) if date_str else base


def parse(html, config, date_str, warn=None):
    """HTMLから {キー: {"name":表示名, "status":状態}} を返す。

    status は core.status の語彙に正規化する:
        st.AVAILABLE  空きあり  ← これだけが通知の対象
        st.FULL       空きなし
        st.PHONE      要問い合わせ
        st.CLOSED     受付不可

    重要: 判定できないときは MonitorError を投げること。
    黙って FULL や UNKNOWN を返すと、サイトの変更に気づけないまま
    「空きなし」と誤記録され、本当に空いたときに通知が飛ばなくなる。

    warn(msg) は「怪しいが致命的ではない」ことを記録する用（任意）。
    """
    warn = warn or (lambda msg: None)
    soup = BeautifulSoup(html, "lxml")

    result = {}
    for key, spec in (config.get("items") or {}).items():
        el = soup.select_one(spec["selector"])
        if el is None:
            raise MonitorError(f"{key}: {spec['selector']} が見つかりません")

        text = el.get_text(strip=True)
        if "空" in text:
            status = st.AVAILABLE
        elif "満" in text:
            status = st.FULL
        else:
            raise MonitorError(f"{key}: 状態を判定できません: {text[:80]!r}")

        result[key] = {"name": spec.get("name") or key, "status": status}

    return result


def discover(html, config, date_str, warn=None):
    """任意。targets.yml に書く項目を洗い出せるようにしておくと後が楽。

    [{"key":..., "name":..., "status":...}] を返す。
    """
    raise MonitorError("このアダプタは discover に対応していません")
