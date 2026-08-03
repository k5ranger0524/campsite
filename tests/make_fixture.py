#!/usr/bin/env python3
"""テスト用のHTMLフィクスチャを生成する。

【重要】ここで生成するHTMLは合成データであり、実サイトから取得したものではない。
ただしヘッダの表記は実サイトの debug.html を確認して合わせてある:

    先頭列   <span>9/19</span><span>土</span>   ← 月付きはここだけ
    2列目以降 <span>20</span><span>日</span>     ← 日のみ

使い方:
    python3 tests/make_fixture.py            # 全パターンを tests/fixtures/ に生成
"""

import os
from datetime import date, timedelta

ROOMS = [
    ("room_24246", "【F1】3家族サイト 大"),
    ("room_24247", "【F2】3家族サイト"),
    ("room_24248", "【F3】3家族サイト 大"),
    ("room_24249", "【F4】3家族サイト 大"),
]

BASE = date(2026, 9, 19)
NDAYS = 14
WEEK = "月火水木金土日"

ICONS = {
    "available": ("fa-regular", "fa-circle"),
    "full": ("fa-solid", "fa-xmark"),
    "phone": ("fa-solid", "fa-square-phone"),
    "closed": ("fa-solid", "fa-minus"),
}


def header(d, first):
    """先頭列だけ月付き。2列目以降は日のみ。"""
    label = f"{d.month}/{d.day}" if first else f"{d.day}"
    return f"<th><div><span>{label}</span><span>{WEEK[d.weekday()]}</span></div></th>"


def cell(status, room_id, d):
    style, icon = ICONS[status]
    i = f'<i class="{style} {icon}"></i>'
    if status == "available":
        # 空きありの td のみリンクを持つ
        href = (
            f"https://reserve.489ban.net/client/autocamp-akagi/0/plan/room/"
            f"{room_id.split('_')[1]}/stay?date={d:%Y-%m-%d}&amp;roomCount=1#chooseFromRoom"
        )
        return f'<td><div><a href="{href}">{i}</a></div></td>'
    return f"<td><div>{i}</div></td>"


def build(statuses_by_room, base=BASE, ndays=NDAYS, room_list=ROOMS, header_days=None):
    """statuses_by_room: {room_id: [ndays個の status]}

    header_days を渡すとヘッダの日付だけ差し替えられる（構造変化のテスト用）。
    """
    days = [base + timedelta(days=i) for i in range(ndays)]
    hdays = header_days or days

    parts = [
        "<!DOCTYPE html><html lang='ja'><head><meta charset='utf-8'>",
        "<title>空室状況</title></head><body>",
        '<ul class="webc_avlbl_list">',
    ]
    for room_id, name in room_list:
        statuses = statuses_by_room[room_id]
        ths = "".join(header(d, i == 0) for i, d in enumerate(hdays))
        tds = "".join(cell(s, room_id, days[i]) for i, s in enumerate(statuses))
        parts.append(f"""
<li id="{room_id}">
  <dl class="webc_avlbl_item"><dt>{name}</dt><dd>1泊</dd></dl>
  <div class="webc_avlbl_cal">
    <table>
      <thead><tr>{ths}</tr></thead>
      <tbody><tr>{tds}</tr></tbody>
    </table>
  </div>
</li>""")
    parts.append("</ul></body></html>")
    return "\n".join(parts)


def main():
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")
    os.makedirs(out, exist_ok=True)

    def write(name, html):
        with open(os.path.join(out, name), "w", encoding="utf-8") as f:
            f.write(html)
        print("生成:", os.path.join(out, name))

    ids = [r[0] for r in ROOMS]

    # 1. 実測値どおり: F1〜F4 すべて 9/19 は空きなし
    #    他の日にはバリエーションを入れる（列の取り違えを検出するため）
    all_full = {rid: ["full"] * NDAYS for rid in ids}
    for rid in ids:
        all_full[rid][2] = "available"    # 9/21
        all_full[rid][5] = "phone"        # 9/24
        all_full[rid][9] = "closed"       # 9/28
        all_full[rid][13] = "available"   # 10/2 (月跨ぎ)
    write("all_full.html", build(all_full))

    # 2. F2 と F3 の 9/19 が空きあり
    some_open = {rid: list(all_full[rid]) for rid in ids}
    some_open["room_24247"][0] = "available"
    some_open["room_24248"][0] = "available"
    write("f2f3_available.html", build(some_open))

    # 3. 表示期間が2日前倒し → 9/19 は列2 になる
    #    どの列を読んだか一意に分かるよう、前後の列と違う状態を置く
    shifted = {rid: ["full"] * NDAYS for rid in ids}
    for rid in ids:
        shifted[rid][1] = "available"   # 9/18
        shifted[rid][2] = "phone"       # 9/19 ← 目的の列
        shifted[rid][3] = "closed"      # 9/20
    write("shifted.html", build(shifted, base=date(2026, 9, 17)))

    # 4. 9/19 が表示範囲に存在しない
    write("date_missing.html", build(all_full, base=date(2026, 10, 5)))

    # 5. F4 が存在しない（構造変化）
    write("room_missing.html", build(all_full, room_list=ROOMS[:3]))

    # 6. 未知のアイコンclass（構造変化）
    write("unknown_icon.html", build(all_full).replace("fa-xmark", "fa-unknown-icon"))

    # 7. ヘッダの日付が飛んでいる（列の増減など構造変化）
    #    先頭は 9/19 のままだが、以降が1日ずつずれている
    drift = [BASE] + [BASE + timedelta(days=i + 2) for i in range(NDAYS - 1)]
    write("header_drift.html", build(all_full, header_days=drift))


if __name__ == "__main__":
    main()
