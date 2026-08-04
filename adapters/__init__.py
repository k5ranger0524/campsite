"""アダプタの登録簿。

新しい予約システムに対応するときは、このディレクトリにモジュールを1つ足すだけでよい。
モジュールが備えるべきもの:

    build_url(config, date) -> str
        取得先URL。通知本文にも載る。

    parse(html, config, date) -> {キー: {"name": str, "status": str}}
        status は core.status の語彙（available / full / phone / closed / unknown）に
        正規化して返す。判定できない場合は例外 MonitorError を投げること
        （state を書かせないため。黙って unknown を返さない）。

    任意: fetch(config, date) -> str
        独自の取得手順が要る場合だけ定義する（POSTが必要、JS描画で
        Playwright が要る、など）。無ければ共通の fetch_html が使われる。

    任意: discover(html, config, date) -> [{"key","name","status"}]
        設定に書く項目を洗い出すための一覧。discover.py から呼ばれる。
"""

import importlib

from core.util import MonitorError

_CACHE = {}


def get(name):
    if name in _CACHE:
        return _CACHE[name]
    try:
        mod = importlib.import_module(f"adapters.{name}")
    except ImportError as exc:
        raise MonitorError(f"アダプタ '{name}' を読み込めません: {exc}")

    for required in ("build_url", "parse"):
        if not hasattr(mod, required):
            raise MonitorError(f"アダプタ '{name}' に {required}() がありません")

    _CACHE[name] = mod
    return mod
