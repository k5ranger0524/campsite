#!/usr/bin/env python3
"""btimes アダプタの異常系テスト用HTMLを生成する。

正常系は実サイトのHTML（captures/btimes*.html）で検証する。
ここで作るのは、実サイトからは取りにくい壊れ方だけ:
    未知のstatus class / 日付属性の欠落 / 表示範囲外 / 表示日付との食い違い

実サイトで確認済みの作りに合わせてある:
    - tr#reserveStatusTr の id は全行で重複
    - 満車の行は input#dateDatetime に、空きの行は time#date に日付がある
"""

import os
from datetime import date, timedelta

WEEK = "月火水木金土日"


def row(d, status, *, date_attr="auto", label=None, badge=None, tr_class=None):
    """1日ぶんの行。

    date_attr: "auto"（状態に応じた実サイトどおりの置き場所）/ "none"（欠落）
    label:     span#useDate の表示。既定はISO日付と一致
    badge:     p#reserveStatus の状態class。既定は status
    """
    badge = badge or status
    tr_class = tr_class or status
    label = label if label is not None else f"{d.month}/{d.day}（{WEEK[d.weekday()]}）"

    if date_attr == "none":
        date_bits = '<time id="use" datetime=""></time>'
    elif status == "full":
        # 満車の行: input#dateDatetime に入る。time#use は空
        date_bits = (
            f'<input id="dateDatetime" type="hidden" value="{d:%Y-%m-%d}">'
            f'<time id="use" datetime=""></time>'
        )
    else:
        # 空きの行: time#date と input#day3-hidden に入る
        date_bits = (
            f'<time id="date" datetime="{d:%Y-%m-%d}">{d.day}</time>'
            f'<input id="day3-hidden" type="hidden" value="{d:%Y%m%d}">'
        )

    detail = ""
    if status == "vaca":
        detail = ('<span id="startTime">15:00</span><span id="endTime">24:00</span>'
                  '<span id="price">1,300</span>')

    return (
        f'<tr id="reserveStatusTr" class="{tr_class}">'
        f'<td>{date_bits}<span id="useDate">{label}</span></td>'
        f'<td><p id="reserveStatus" class="l-main-status-badge l-main-status-{badge}">'
        f'{"満車" if badge == "full" else "空きあり"}</p></td>'
        f'<td>{detail}</td>'
        f'</tr>'
    )


def page(rows):
    return (
        '<!DOCTYPE html><html lang="ja"><head><meta charset="utf-8">'
        '<title>タイムズのB</title></head><body>'
        '<table><tbody>' + "".join(rows) + '</tbody></table>'
        '</body></html>'
    )


def window(start, days=14, statuses=None):
    """start から days 日ぶん。statuses で個別に上書きできる。"""
    statuses = statuses or {}
    return [
        row(start + timedelta(days=i), statuses.get(i, "full"))
        for i in range(days)
    ]


def main():
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")
    os.makedirs(out, exist_ok=True)

    def write(name, html):
        with open(os.path.join(out, name), "w", encoding="utf-8") as f:
            f.write(html)
        print("生成:", os.path.join(out, name))

    start = date(2026, 8, 9)          # 8/9〜8/22 の14日窓

    # 1. 正常: 8/22 満車、8/18 空き（実データと同じ形）
    write("btimes_ok.html", page(window(start, statuses={9: "vaca"})))

    # 2. 未知の status class
    rows = window(start)
    rows[13] = row(date(2026, 8, 22), "full", badge="maintenance", tr_class="full")
    write("btimes_unknown_status.html", page(rows))

    # 3. 日付属性がどこにも無い行
    rows = window(start)
    rows[13] = row(date(2026, 8, 22), "full", date_attr="none")
    write("btimes_no_date.html", page(rows))

    # 4. 表示範囲外（8/22 を含まない窓）
    write("btimes_out_of_range.html", page(window(date(2026, 8, 1))))

    # 5. ISO日付と span#useDate が食い違う
    rows = window(start)
    rows[13] = row(date(2026, 8, 22), "full", label="9/30（水）")
    write("btimes_label_mismatch.html", page(rows))

    # 6. tr の class とバッジが食い違う（警告のみ。判定はバッジ側）
    rows = window(start)
    rows[13] = row(date(2026, 8, 22), "vaca", badge="vaca", tr_class="full")
    write("btimes_class_conflict.html", page(rows))

    # 7. 行が1つも無い
    write("btimes_empty.html", page([]))


if __name__ == "__main__":
    main()
