"""空き状況の共通語彙。

キャンプ場・駐車場・ホテルなど対象が変わっても、アダプタはこの語彙に正規化して返す。
通知の対象になるのは AVAILABLE だけ。
"""

AVAILABLE = "available"
FULL = "full"
PHONE = "phone"
CLOSED = "closed"
UNKNOWN = "unknown"

LABEL = {
    AVAILABLE: "空きあり",
    FULL: "空きなし",
    PHONE: "要問い合わせ",
    CLOSED: "受付不可",
    UNKNOWN: "不明",
}

ALL = tuple(LABEL)


def label(status):
    return LABEL.get(status, LABEL[UNKNOWN])


def is_available(status):
    return status == AVAILABLE
