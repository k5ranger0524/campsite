#!/usr/bin/env python3
"""テスト用のHTMLフィクスチャを生成する。

【重要】ここで生成するHTMLは、指示で与えられたHTML構造の仕様に基づいて
私が組み立てた「合成データ」であり、実サイトから取得したものではない。
monitor.py のパース処理・状態遷移・エラー処理が動くことの確認にしか使えない。
仕様そのものが実サイトと一致しているかは、実サイトの debug.html でしか検証できない。

使い方:
    python3 tests/make_fixture.py            # 全パターンを tests/fixtures/ に生成
"""

import os

ROOMS = [
    ("room_24246", "【F1】3家族サイト 大"),
    ("room_24247", "【F2】3家族サイト"),
    ("room_24248", "【F3】3家族サイト 大"),
    ("room_24249", "【F4】3家族サイト 大"),
]

# 14日分の日付ラベルと曜日
DAYS = [
    ("9/19", "土"), ("9/20", "日"), ("9/21", "月"), ("9/22", "火"),
    ("9/23", "水"), ("9/24", "木"), ("9/25", "金"), ("9/26", "土"),
    ("9/27", "日"), ("9/28", "月"), ("9/29", "火"), ("9/30", "水"),
    ("10/1", "木"), ("10/2", "金"),
]

ICONS = {
    "available": "fa-circle",
    "full": "fa-xmark",
    "phone": "fa-square-phone",
    "closed": "fa-minus",
}


def cell(status, day):
    icon = f'<i class="fa-solid {ICONS[status]}"></i>'
    if status == "available":
        # 空きありの td のみリンクを持つ
        href = f"/client/autocamp-akagi/0/plan/reserve?room=1&amp;date=2026-{day.replace('/', '-')}"
        return f'<td><a href="{href}">{icon}</a></td>'
    return f"<td>{icon}</td>"


def build(statuses_by_room, days=DAYS, room_list=ROOMS):
    """statuses_by_room: {room_id: [14個の status]}"""
    parts = [
        "<!DOCTYPE html><html lang='ja'><head><meta charset='utf-8'>",
        "<title>空室状況</title></head><body>",
        '<ul class="webc_avlbl_list">',
    ]
    for room_id, name in room_list:
        statuses = statuses_by_room[room_id]
        ths = "".join(
            f"<th><span>{d}</span><span>{w}</span></th>" for d, w in days
        )
        tds = "".join(cell(s, days[i][0]) for i, s in enumerate(statuses))
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


def uniform(status, n=14):
    return [status] * n


def main():
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")
    os.makedirs(out, exist_ok=True)

    def write(name, html):
        path = os.path.join(out, name)
        with open(path, "w", encoding="utf-8") as f:
            f.write(html)
        print("生成:", path)

    ids = [r[0] for r in ROOMS]

    # 1. 実測値どおり: F1〜F4 すべて 9/19 は空きなし
    all_full = {rid: uniform("full") for rid in ids}
    # 他の日にはバリエーションを入れておく（列の取り違えを検出するため）
    for rid in ids:
        all_full[rid][1] = "available"   # 9/20 は空きあり
        all_full[rid][5] = "phone"
        all_full[rid][9] = "closed"
    write("all_full.html", build(all_full))

    # 2. F2 と F3 の 9/19 が空きあり
    some_open = {rid: list(all_full[rid]) for rid in ids}
    some_open["room_24247"][0] = "available"
    some_open["room_24248"][0] = "available"
    write("f2f3_available.html", build(some_open))

    # 3. 表示期間がずれて 9/19 が先頭でない（列探索が効くか）
    shifted_days = [("9/17", "木"), ("9/18", "金")] + DAYS[:12]
    write("shifted.html", build(all_full, days=shifted_days))

    # 4. 9/19 が表示範囲に存在しない
    no_target = [(f"10/{i + 5}", "日") for i in range(14)]
    write("date_missing.html", build(all_full, days=no_target))

    # 5. F4 が存在しない（構造変化）
    write("room_missing.html", build(all_full, room_list=ROOMS[:3]))

    # 6. 未知のアイコンclass（構造変化）
    broken = build(all_full).replace("fa-xmark", "fa-unknown-icon")
    write("unknown_icon.html", broken)


if __name__ == "__main__":
    main()
