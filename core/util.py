"""共通のユーティリティ。監視対象に依存しない。"""

from datetime import datetime, timedelta, timezone

JST = timezone(timedelta(hours=9))


class MonitorError(Exception):
    """state を更新してはいけない異常。対象単位で捕捉され、最終的に exit 1 になる。"""


def now_jst():
    return datetime.now(JST)


def log(msg):
    print(f"[{now_jst():%Y-%m-%d %H:%M:%S}] {msg}", flush=True)
