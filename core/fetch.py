"""HTTP取得。監視対象に依存しない。"""

import time

import requests

from .util import MonitorError, log

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

DEFAULT_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,*/*;q=0.8"
    ),
    "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
    "Upgrade-Insecure-Requests": "1",
}


def fetch_html(url, headers=None, timeout=30, attempts=4):
    """指数バックオフ付きで取得する。失敗しきったら MonitorError。"""
    merged = dict(DEFAULT_HEADERS)
    if headers:
        merged.update(headers)

    delay = 2
    last = None
    for attempt in range(attempts):
        try:
            resp = requests.get(url, headers=merged, timeout=timeout)
            resp.raise_for_status()
            if "charset=" not in resp.headers.get("Content-Type", "").lower():
                resp.encoding = resp.apparent_encoding or "utf-8"
            return resp.text
        except requests.RequestException as exc:
            last = exc
            if attempt < attempts - 1:
                log(f"  取得失敗 ({exc.__class__.__name__}): {delay}秒後に再試行")
                time.sleep(delay)
                delay *= 2
    raise MonitorError(f"ページ取得に失敗しました: {last}")
