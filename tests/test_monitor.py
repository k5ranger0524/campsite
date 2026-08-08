#!/usr/bin/env python3
"""回帰テスト。

入力は tests/fixtures/ の合成HTML（ban489 のヘッダ表記だけは実サイトの debug.html に
合わせてある）。ntfy.sh への送信と HTTP取得は差し替えて検証するので、通信は行わない。

実行: python3 tests/test_monitor.py
"""

import json
import os
import shutil
import sys
import tempfile
from datetime import datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

import monitor                      # noqa: E402
from core import notify as nt       # noqa: E402

FIX = os.path.join(HERE, "fixtures")

PASS = 0
FAIL = 0
SENT = []
SEND_SHOULD_FAIL = False
ROUTES = {}                         # URL -> fixture ファイル名


def fake_send_ntfy(topic, title, message, url):
    if SEND_SHOULD_FAIL:
        raise nt.requests.RequestException("接続失敗(テスト)")
    SENT.append({"topic": topic, "title": title, "message": message, "url": url})


def fake_fetch(url, *a, **k):
    for frag, name in ROUTES.items():
        if frag in url:
            with open(os.path.join(FIX, name), encoding="utf-8") as f:
                return f.read()
    raise nt.MonitorError(f"テスト: 未登録のURL {url}")


nt.send_ntfy = fake_send_ntfy
monitor.fetch_html = fake_fetch


def check(desc, cond):
    global PASS, FAIL
    if cond:
        print(f"  ok   : {desc}")
        PASS += 1
    else:
        print(f"  FAIL : {desc}")
        FAIL += 1


def eq(desc, actual, expected):
    check(desc if actual == expected else f"{desc} (期待={expected!r}, 実際={actual!r})",
          actual == expected)


def write_targets(path, body):
    with open(path, "w", encoding="utf-8") as f:
        f.write(body)
    return path


def run(targets_path, state, *extra, topic="test-topic"):
    SENT.clear()
    argv = ["monitor.py", "--targets", targets_path, "--state", state] + list(extra)
    old_argv, old_topic = sys.argv, os.environ.get("NTFY_TOPIC")
    sys.argv = argv
    if topic is None:
        os.environ.pop("NTFY_TOPIC", None)
    else:
        os.environ["NTFY_TOPIC"] = topic
    try:
        code = monitor.main()
    finally:
        sys.argv = old_argv
        if old_topic is None:
            os.environ.pop("NTFY_TOPIC", None)
        else:
            os.environ["NTFY_TOPIC"] = old_topic
    return code, list(SENT)


def read_state(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def items_of(path, target_id):
    return read_state(path)["targets"][target_id]["items"]


def backdate(path, target_id, key, hours):
    data = read_state(path)
    t = datetime.fromisoformat(data["targets"][target_id]["items"][key]["notified_at"])
    data["targets"][target_id]["items"][key]["notified_at"] = (
        t - timedelta(hours=hours)).isoformat()
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


AKAGI = """
targets:
  - id: akagi
    name: 赤城山オートキャンプ場 3家族サイト
    adapter: ban489
    date: {date}
    config:
      facility: autocamp-akagi
      items:
        F1: room_24246
        F2: room_24247
        F3: room_24248
        F4: room_24249
"""

PARKING = """
targets:
  - id: parking
    name: デモ駐車場
    adapter: css
    config:
      url: https://example.com/parking
      items:
        P1: {selector: "#lot-1 .status", name: 第1駐車場}
        P2: {selector: "#lot-2 .status", name: 第2駐車場}
        P3: {selector: "#lot-3 .status", name: 第3駐車場}
        P4: {selector: "#lot-4 .status", name: 第4駐車場}
      rules:
        - {contains: 満車, status: full}
        - {contains: 空車, status: available}
        - {regex: '残り\\s*[1-9]', status: available}
      default: closed
"""


def main():
    global SEND_SHOULD_FAIL

    import make_fixture
    make_fixture.main()
    print()

    tmp = tempfile.mkdtemp()
    state = os.path.join(tmp, "state.json")
    akagi = write_targets(os.path.join(tmp, "akagi.yml"), AKAGI.format(date="2026-09-19"))

    def go(fixture, *extra, **kw):
        return run(akagi, state, "--target", "akagi",
                   "--file", os.path.join(FIX, fixture),
                   "--write-state-from-file", *extra, **kw)

    print("== 1. 全区画が空きなし（実測値どおりか） ==")
    code, sent = go("all_full.html")
    eq("exit 0", code, 0)
    eq("通知なし", len(sent), 0)
    for k in ["F1", "F2", "F3", "F4"]:
        eq(f"{k} が full", items_of(state, "akagi")[k]["status"], "full")
    eq("notified_at は None", items_of(state, "akagi")["F1"]["notified_at"], None)

    print("\n== 2. F2/F3 が空きあり → 新規通知（exit 0） ==")
    code, sent = go("f2f3_available.html")
    eq("通知しても exit 0", code, 0)
    eq("1件送信", len(sent), 1)
    msg = sent[0]["message"]
    check("タイトルに対象名が入る", sent[0]["title"] == "赤城山オートキャンプ場 3家族サイト 空き通知")
    check("F2/F3 を含む", "・F2" in msg and "・F3" in msg)
    check("空きなしの F1 を含まない", "・F1" not in msg)
    check("予約URLを含む", "date=2026-09-19" in msg)
    check("継続中とは書かない", "継続中" not in msg)
    check("F2 に notified_at", items_of(state, "akagi")["F2"]["notified_at"] is not None)

    print("\n== 3. 2時間以内の再実行 → 再通知しない ==")
    code, sent = go("f2f3_available.html")
    eq("exit 0", code, 0)
    eq("通知なし", len(sent), 0)

    print("\n== 4. 2時間経過 → 継続中として再通知 ==")
    backdate(state, "akagi", "F2", 3)
    backdate(state, "akagi", "F3", 3)
    code, sent = go("f2f3_available.html")
    eq("1件送信", len(sent), 1)
    check("タイトルが継続中", sent[0]["title"].endswith("空き継続中"))
    check("本文に継続中", "継続中" in sent[0]["message"])
    check("再通知間隔の説明", "2 時間ごと" in sent[0]["message"])

    print("\n== 5. 片方だけ2時間経過 → そのサイトだけ再通知 ==")
    backdate(state, "akagi", "F3", 3)
    code, sent = go("f2f3_available.html")
    msg = sent[0]["message"]
    check("F3 は載る", "・F3" in msg)
    check("F2 は載らない", "・F2" not in msg)

    print("\n== 6. 空きが消えると通知履歴がリセットされる ==")
    code, sent = go("all_full.html")
    eq("通知なし", len(sent), 0)
    eq("F2 リセット", items_of(state, "akagi")["F2"]["notified_at"], None)

    print("\n== 7. 再び空いたら『新規』として通知 ==")
    code, sent = go("f2f3_available.html")
    check("タイトルが空き通知", sent[0]["title"].endswith("空き通知"))
    check("継続中ではない", "継続中" not in sent[0]["message"])

    print("\n== 8. 表示期間がずれても目的日を探せる ==")
    s2 = os.path.join(tmp, "shifted.json")
    code, sent = run(akagi, s2, "--target", "akagi",
                     "--file", os.path.join(FIX, "shifted.html"),
                     "--write-state-from-file")
    eq("exit 0", code, 0)
    eq("列2(9/19)を読んでいる", items_of(s2, "akagi")["F1"]["status"], "phone")

    print("\n== 8b. 先頭列以外の日付も引ける（月付き表記が無い列） ==")
    for date, expect, label in [("2026-09-21", "available", "9/21"),
                                ("2026-10-02", "available", "10/2 (月跨ぎ)")]:
        y = write_targets(os.path.join(tmp, f"d{date}.yml"), AKAGI.format(date=date))
        s = os.path.join(tmp, f"d{date}.json")
        code, _ = run(y, s, "--target", "akagi",
                      "--file", os.path.join(FIX, "all_full.html"),
                      "--write-state-from-file")
        eq(f"{label} は exit 0", code, 0)
        eq(f"{label} の判定", items_of(s, "akagi")["F1"]["status"], expect)

    y = write_targets(os.path.join(tmp, "oor.yml"), AKAGI.format(date="2026-10-03"))
    s = os.path.join(tmp, "oor.json")
    code, _ = run(y, s, "--target", "akagi", "--file", os.path.join(FIX, "all_full.html"),
                  "--write-state-from-file")
    eq("表示期間外は exit 1", code, 1)
    check("state を作らない", not os.path.exists(s))

    print("\n== 9. NTFY_TOPIC 未設定は起動直後に exit 1 ==")
    s3 = os.path.join(tmp, "cfg.json")
    ROUTES.clear(); ROUTES["autocamp-akagi"] = "all_full.html"
    code, _ = run(akagi, s3, topic=None)
    eq("exit 1", code, 1)
    check("state を作らない", not os.path.exists(s3))
    code, _ = run(akagi, s3, "--target", "akagi",
                  "--file", os.path.join(FIX, "all_full.html"),
                  "--test-notify", topic=None)
    eq("--test-notify でも exit 1", code, 1)

    print("\n== 10. 異常系は exit 1 かつ state を更新しない ==")
    code, _ = go("all_full.html")
    before = open(state, encoding="utf-8").read()
    for fx in ["date_missing.html", "room_missing.html", "unknown_icon.html"]:
        code, sent = go(fx)
        eq(f"{fx} は exit 1", code, 1)
        eq(f"{fx}: 通知しない", len(sent), 0)
        check(f"{fx}: state 未変更", open(state, encoding="utf-8").read() == before)

    code, _ = go("does_not_exist.html")
    eq("存在しないファイルは exit 1", code, 1)
    check("state 未変更", open(state, encoding="utf-8").read() == before)

    drift = write_targets(os.path.join(tmp, "drift.yml"), AKAGI.format(date="2026-09-21"))
    code, _ = run(drift, state, "--target", "akagi",
                  "--file", os.path.join(FIX, "header_drift.html"),
                  "--write-state-from-file")
    eq("ヘッダ日付のずれを検出して exit 1", code, 1)
    check("state 未変更", open(state, encoding="utf-8").read() == before)

    print("\n== 11. ntfy 送信失敗は exit 1 かつ state を更新しない ==")
    SEND_SHOULD_FAIL = True
    code, _ = go("f2f3_available.html")
    SEND_SHOULD_FAIL = False
    eq("exit 1", code, 1)
    check("state 未変更（次回やり直す）", open(state, encoding="utf-8").read() == before)

    code, sent = go("f2f3_available.html")
    eq("次の回は通知をやり直す", len(sent), 1)

    print("\n== 12. テスト通知は本物と混同されない ==")
    before = open(state, encoding="utf-8").read()
    code, sent = go("all_full.html", "--test-notify")
    eq("exit 0", code, 0)
    msg = sent[0]["message"]
    check("タイトルに【テスト】", sent[0]["title"].startswith("【テスト】"))
    check("テストである旨を明記", "これは通知テストです" in msg)
    check("『空きが出ました』と書かない", "空きが出ました" not in msg)
    check("実際の判定結果を載せる", "・F1  空きなし" in msg)
    check("対象名を載せる", "赤城山オートキャンプ場" in msg)
    check("state 未変更", open(state, encoding="utf-8").read() == before)

    code, sent = go("f2f3_available.html", "--test-notify")
    msg = sent[0]["message"]
    check("空きありでも『空きが出ました』と書かない", "空きが出ました" not in msg)
    check("F2 は空きありと表示", "・F2  空きあり" in msg)

    print("\n== 13. --file 単体では state を更新しない（既定動作） ==")
    s4 = os.path.join(tmp, "nofile.json")
    code, _ = run(akagi, s4, "--target", "akagi",
                  "--file", os.path.join(FIX, "all_full.html"))
    eq("exit 0", code, 0)
    check("state を作らない", not os.path.exists(s4))

    # ------------------------------------------------------------------
    print("\n== 14. cssアダプタ: 設定だけで別ジャンル（駐車場）を監視できる ==")
    parking = write_targets(os.path.join(tmp, "parking.yml"), PARKING)
    s5 = os.path.join(tmp, "parking.json")
    code, sent = run(parking, s5, "--target", "parking",
                     "--file", os.path.join(FIX, "parking.html"),
                     "--write-state-from-file")
    eq("exit 0", code, 0)
    items = items_of(s5, "parking")
    eq("満車 → full", items["P1"]["status"], "full")
    eq("空車 → available", items["P2"]["status"], "available")
    eq("『残り 3 台』→ available (regex)", items["P3"]["status"], "available")
    eq("どのruleにも一致しない → default", items["P4"]["status"], "closed")
    eq("1件通知", len(sent), 1)
    msg = sent[0]["message"]
    check("日付なし対象では日付を書かない", "に空きが出ました" not in msg)
    check("空きが出た旨は書く", "空きが出ました。" in msg)
    check("設定した名前が出る", "第2駐車場" in msg)

    print("\n== 14b. default 未指定でどのruleにも一致しなければ失敗する ==")
    nodefault = write_targets(
        os.path.join(tmp, "nodef.yml"), PARKING.replace("      default: closed\n", ""))
    s6 = os.path.join(tmp, "nodef.json")
    code, sent = run(nodefault, s6, "--target", "parking",
                     "--file", os.path.join(FIX, "parking.html"),
                     "--write-state-from-file")
    eq("exit 1（黙って unknown にしない）", code, 1)
    check("state を作らない", not os.path.exists(s6))

    # ------------------------------------------------------------------
    print("\n== 15. 複数対象: 1つ壊れても他は確認され、壊れた方だけ state を残す ==")
    multi = write_targets(os.path.join(tmp, "multi.yml"), """
targets:
  - id: good
    name: 正常な対象
    adapter: ban489
    date: 2026-09-19
    config:
      facility: good-camp
      items: {F2: room_24247}
  - id: broken
    name: 壊れた対象
    adapter: ban489
    date: 2026-09-19
    config:
      facility: broken-camp
      items: {F9: room_99999}
""")
    ROUTES.clear()
    ROUTES["good-camp"] = "f2f3_available.html"      # F2 は空きあり → 通知される
    ROUTES["broken-camp"] = "all_full.html"          # room_99999 が無い → 失敗する
    s7 = os.path.join(tmp, "multi.json")
    code, sent = run(multi, s7)
    eq("全体は exit 1", code, 1)
    eq("正常な対象の通知は送られる", len(sent), 1)
    check("正常な対象の state は書かれる", "good" in read_state(s7)["targets"])
    check("壊れた対象の state は書かれない", "broken" not in read_state(s7)["targets"])

    print("\n== 16. targets.yml の検証 ==")
    bad = write_targets(os.path.join(tmp, "bad.yml"), """
targets:
  - id: a
    adapter: ban489
  - id: a
    adapter: ban489
""")
    code, _ = run(bad, os.path.join(tmp, "b.json"))
    eq("id重複は exit 1", code, 1)

    noadapter = write_targets(os.path.join(tmp, "na.yml"), "targets:\n  - id: a\n")
    code, _ = run(noadapter, os.path.join(tmp, "b.json"))
    eq("adapter 無しは exit 1", code, 1)

    unknown = write_targets(os.path.join(tmp, "uk.yml"),
                            "targets:\n  - id: a\n    adapter: nonexistent\n")
    code, _ = run(unknown, os.path.join(tmp, "b.json"))
    eq("未知のadapterは exit 1", code, 1)

    print("\n== 17. discover で設定に書く項目を洗い出せる ==")
    import adapters
    ban489 = adapters.get("ban489")
    with open(os.path.join(FIX, "all_full.html"), encoding="utf-8") as f:
        found = ban489.discover(f.read(), {}, "2026-09-19")
    eq("4区画すべて見つかる", len(found), 4)
    check("IDが取れる", found[0]["key"].startswith("room_"))
    check("名前が取れる", "3家族サイト" in found[0]["name"])

    css = adapters.get("css")
    with open(os.path.join(FIX, "parking.html"), encoding="utf-8") as f:
        found = css.discover(f.read(), {"discover_selector": ".parking-lot"}, None)
    eq("駐車場4つが見つかる", len(found), 4)
    check("IDが取れる", found[0]["key"] == "lot-1")

    # ------------------------------------------------------------------
    print("\n== 18. 羽田駐車場アダプタ（実際に取得したAPIデータで検証） ==")
    cap = os.path.join(ROOT, "captures", "haneda-render.api.json")
    if not os.path.exists(cap):
        check("キャプチャが無いのでスキップ", True)
    else:
        hnd = adapters.get("haneda_parking")
        cfg = {
            "areas": {"P2": {"id": 0, "name": "第2駐車場"},
                      "P3": {"id": 1, "name": "第3駐車場"}},
            "dates": ["2026-08-22", "2026-08-23", "2026-08-24", "2026-08-25"],
            "handicapped": 0,
        }
        with open(cap, encoding="utf-8") as f:
            raw = f.read()

        items = hnd.parse(raw, cfg, None)
        eq("2駐車場 × 4日 = 8件", len(items), 8)
        # 取得時点の実際の値: P2 8/23 だけ konzatsu、他は full
        eq("P2 8/23 は混雑 → 空きあり", items["P2 8/23"]["status"], "available")
        eq("P2 8/22 は満車", items["P2 8/22"]["status"], "full")
        eq("P3 8/23 は満車", items["P3 8/23"]["status"], "full")
        avail = [k for k, v in items.items() if v["status"] == "available"]
        eq("空きありは1件だけ", avail, ["P2 8/23"])
        check("表示名に日付が入る", items["P2 8/23"]["name"].endswith("8/23"))

        # 混雑を通知対象から外す設定
        items2 = hnd.parse(raw, dict(cfg, status_map={"konzatsu": "full"}), None)
        eq("混雑を除外すると空きなしになる", items2["P2 8/23"]["status"], "full")

        # 期間外（受付開始前）は closed
        far = hnd.parse(raw, dict(cfg, dates=["2026-09-04"]), None)
        eq("期間外は closed", far["P2 9/4"]["status"], "closed")

        # 未知のstatus（＝空車の可能性）は空きあり扱いにし、警告を出す
        warned = []
        # api.json は body がJSON文字列として入れ子になっているので素朴に置換する
        spoofed = raw.replace("konzatsu", "vacant")
        items3 = hnd.parse(spoofed, cfg, None, warn=warned.append)
        eq("未知のstatusは空きあり扱い", items3["P2 8/23"]["status"], "available")
        check("警告を出す", any("vacant" in w for w in warned))

        # カレンダーに無い日を指定したら失敗させる（黙って見逃さない）
        try:
            hnd.parse(raw, dict(cfg, dates=["2027-01-01"]), None)
            check("範囲外の日付は失敗する", False)
        except Exception as exc:
            check("範囲外の日付は失敗する", "含まれていません" in str(exc))

    # ------------------------------------------------------------------
    print("\n== 19. group で確認頻度を分けられる ==")
    grouped = write_targets(os.path.join(tmp, "grp.yml"), """
defaults:
  group: normal
targets:
  - id: slow
    name: ゆっくり確認する対象
    adapter: ban489
    date: 2026-09-19
    config:
      facility: slow-camp
      items: {F1: room_24246}
  - id: quick
    name: 頻繁に確認する対象
    adapter: ban489
    date: 2026-09-19
    group: fast
    config:
      facility: quick-camp
      items: {F1: room_24246}
""")
    ROUTES.clear()
    ROUTES["slow-camp"] = "all_full.html"
    ROUTES["quick-camp"] = "all_full.html"

    s8 = os.path.join(tmp, "g-normal.json")
    code, _ = run(grouped, s8, "--group", "normal")
    eq("exit 0", code, 0)
    check("normal だけが対象", list(read_state(s8)["targets"]) == ["slow"])

    s9 = os.path.join(tmp, "g-fast.json")
    code, _ = run(grouped, s9, "--group", "fast")
    eq("exit 0", code, 0)
    check("fast だけが対象", list(read_state(s9)["targets"]) == ["quick"])

    code, _ = run(grouped, os.path.join(tmp, "g-none.json"), "--group", "nonexistent")
    eq("存在しないgroupは exit 1", code, 1)

    print("\n== 20. 変化が無ければ state を書き換えない（短い間隔でも履歴が膨らまない） ==")
    s10 = os.path.join(tmp, "nochange.json")
    ROUTES.clear(); ROUTES["autocamp-akagi"] = "all_full.html"
    code, _ = run(akagi, s10)
    first = open(s10, encoding="utf-8").read()
    check("1回目で作られる", os.path.exists(s10))

    code, _ = run(akagi, s10)
    eq("2回目も exit 0", code, 0)
    check("中身が1バイトも変わらない（更新時刻も進まない）",
          open(s10, encoding="utf-8").read() == first)

    # 状態が変われば当然書き換わる
    ROUTES["autocamp-akagi"] = "f2f3_available.html"
    code, sent = run(akagi, s10)
    eq("空きが出たら通知", len(sent), 1)
    check("state は書き換わる", open(s10, encoding="utf-8").read() != first)

    # ------------------------------------------------------------------
    print("\n== 21. 羽田P4/P5アダプタ（実際に取得したHTMLで検証） ==")
    p4 = os.path.join(ROOT, "captures", "p4.html")
    p5 = os.path.join(ROOT, "captures", "p5.html")
    if not (os.path.exists(p4) and os.path.exists(p5)):
        check("キャプチャが無いのでスキップ", True)
    else:
        hp = adapters.get("haneda_p4p5")
        base = {"url": "https://example.invalid/",
                "kinds": {"一般": "public", "個室": "private"}}
        target_dates = ["2026-08-22", "2026-08-23", "2026-08-24", "2026-08-25"]
        html4 = open(p4, encoding="utf-8").read()
        html5 = open(p5, encoding="utf-8").read()

        for label, html in (("P4", html4), ("P5", html5)):
            items = hp.parse(html, dict(base, dates=target_dates), None)
            eq(f"{label}: 2種別 × 4日 = 8件", len(items), 8)
            eq(f"{label}: 取得時点は全て満車",
               sorted({v["status"] for v in items.values()}), ["full"])
            check(f"{label}: 身障者枠を含まない",
                  not any("身障者" in k for k in items))

        # 日のずれは静かな誤読になるので、状態が違う日で位置を固定する
        for label, html, date, key, expect in [
            ("P4", html4, "2026-08-05", "一般 8/5", "congestion"),
            ("P4", html4, "2026-08-06", "一般 8/6", "full"),
            ("P4", html4, "2026-08-31", "個室 8/31", "congestion"),   # 月末
            ("P5", html5, "2026-08-28", "個室 8/28", "congestion"),
            ("P5", html5, "2026-08-02", "一般 8/2", "unavailable"),   # 期間外
        ]:
            got = hp.parse(html, dict(base, dates=[date]), None)[key]
            eq(f"{label} {key} の生値", got["raw"], expect)

        eq("混雑は空きあり扱い",
           hp.parse(html4, dict(base, dates=["2026-08-05"]), None)["一般 8/5"]["status"],
           "available")
        eq("期間外は closed",
           hp.parse(html5, dict(base, dates=["2026-08-02"]), None)["一般 8/2"]["status"],
           "closed")

        # 身障者枠も明示すれば読める（P5のみ。今回は使わないが取り違えの確認）
        h = hp.parse(html5, {"url": "x", "kinds": {"身障": "handicap"},
                             "dates": ["2026-08-16"]}, None)
        eq("身障者枠は別カレンダーを読む", h["身障 8/16"]["raw"], "congestion")

        # P4 には身障者カレンダーが無いので、指定したら失敗させる
        try:
            hp.parse(html4, {"url": "x", "kinds": {"身障": "handicap"},
                             "dates": ["2026-08-22"]}, None)
            check("P4に無い種別は失敗する", False)
        except Exception as exc:
            check("P4に無い種別は失敗する", "ありません" in str(exc))

        # 表示中の月と違う月を指定したら、黙って別の日を読まずに失敗する
        try:
            hp.parse(html4, dict(base, dates=["2026-09-22"]), None)
            check("別の月は失敗する", False)
        except Exception as exc:
            check("別の月は失敗する", "月" in str(exc))

    # ------------------------------------------------------------------
    print("\n== 22. min_available: 同じ枠で N 日以上空いたときだけ通知する ==")
    from core.state import StateStore, decide_notifications
    from core.util import now_jst

    def items_from(spec):
        """spec: {"一般": [status,...], "個室": [...]} を items 形式にする"""
        out = {}
        for series, statuses in spec.items():
            for i, stt in enumerate(statuses, start=22):
                out[f"{series} 8/{i}"] = {
                    "name": f"{series} 8/{i}", "status": stt,
                    "series": series, "series_name": series,
                }
        return out

    A, F = "available", "full"
    now = now_jst()

    # 2日しか空いていない → 通知しない
    new, cont, ok = decide_notifications(
        items_from({"一般": [A, A, F, F]}), {}, now, 2, min_available=3)
    eq("2日では通知しない", (new, cont), ([], []))
    eq("通知対象も空", ok, set())

    # 3日空いた → 空いている3日がまとめて通知される
    new, cont, ok = decide_notifications(
        items_from({"一般": [A, A, A, F]}), {}, now, 2, min_available=3)
    eq("3日で通知する", sorted(new), ["一般 8/22", "一般 8/23", "一般 8/24"])
    eq("満車の日は入らない", "一般 8/25" in new, False)

    # 枠ごとに数える: 別々の枠に1日ずつでは足りない
    new, cont, ok = decide_notifications(
        items_from({"一般": [A, A, F, F], "個室": [F, F, A, A]}), {}, now, 2,
        min_available=3)
    eq("枠をまたいだ合計では通知しない", (new, cont), ([], []))

    # 片方の枠だけ条件を満たす
    new, cont, ok = decide_notifications(
        items_from({"一般": [A, A, A, A], "個室": [A, F, F, F]}), {}, now, 2,
        min_available=3)
    eq("条件を満たした枠だけ通知", sorted(new), [f"一般 8/{d}" for d in (22, 23, 24, 25)])
    check("満たさない枠は入らない", not any(k.startswith("個室") for k in new))

    # min_available=1 なら従来どおり
    new, cont, ok = decide_notifications(
        items_from({"一般": [A, F, F, F]}), {}, now, 2, min_available=1)
    eq("既定(1)では1日でも通知", new, ["一般 8/22"])

    print("\n== 22b. 条件を下回ったら履歴が消え、戻ったらまとめて再通知 ==")
    s11 = os.path.join(tmp, "minavail.yml")
    write_targets(s11, """
targets:
  - id: park
    name: デモ駐車場
    adapter: css
    min_available: 3
    config:
      url: https://example.com/parking
      items:
        P1: {selector: "#lot-1 .status", name: 第1駐車場}
      rules:
        - {contains: 満車, status: full}
        - {contains: 空車, status: available}
      default: closed
""")
    st10 = os.path.join(tmp, "minavail.json")
    store = StateStore(st10)

    three = items_from({"一般": [A, A, A, F]})
    two = items_from({"一般": [A, A, F, F]})

    new, cont, ok = decide_notifications(three, {}, now, 2, min_available=3)
    store.update("park", three, new + cont, now, None, eligible=ok)
    eq("3日で通知した", len(new), 3)

    prev = store.items_for("park")
    new, cont, ok = decide_notifications(two, prev, now, 2, min_available=3)
    eq("2日に減ったら通知しない", (new, cont), ([], []))
    store.update("park", two, [], now, None, eligible=ok)
    eq("残った空きの履歴も消える",
       [v["notified_at"] for v in store.items_for("park").values()], [None] * 4)

    prev = store.items_for("park")
    new, cont, ok = decide_notifications(three, prev, now, 2, min_available=3)
    eq("3日に戻ったら3日ぶんまとめて再通知", len(new), 3)

    # ------------------------------------------------------------------
    print("\n== 23. btimes アダプタ ==")
    import make_btimes_fixture
    make_btimes_fixture.main()

    bt = adapters.get("btimes")
    bcfg = {"pref": "tokyo", "park_id": 55937, "name": "変なホテル東京羽田駐車場"}

    def bt_parse(fixture, date, **extra):
        path = fixture if os.path.isabs(fixture) else os.path.join(FIX, fixture)
        with open(path, encoding="utf-8") as f:
            return bt.parse(f.read(), dict(bcfg, **extra), date, warn=lambda m: None)

    eq("URLを組み立てる", bt.build_url(bcfg, None),
       "https://btimes.jp/tokyo/park/55937/")

    live = os.path.join(ROOT, "captures", "btimes-live.html")
    if not os.path.exists(live):
        check("実サイトのHTMLが無いのでスキップ", True)
    else:
        with open(live, encoding="utf-8") as f:
            live_html = f.read()
        rows = bt.discover(live_html, bcfg, None)
        eq("実HTML: 14行すべて認識", len(rows), 14)
        eq("実HTML: 8/09〜8/22 が日付付きで並ぶ",
           [r["key"] for r in rows],
           [f"2026-08-{d:02d}" for d in range(9, 23)])
        eq("実HTML: 8/22 は満車",
           bt_parse(live, "2026-08-22")["8/22"]["status"], "full")
        got = bt_parse(live, "2026-08-18")["8/18"]
        eq("実HTML: 8/18 は空きあり", got["status"], "available")
        check("空きの行には時間帯と料金が入る",
              "15:00〜24:00" in got["name"] and "1,300円" in got["name"])

    # 別の駐車場でも同じアダプタで読めること（設定だけで増やせる作りの確認）
    other = os.path.join(ROOT, "captures", "btimes-43295.html")
    if os.path.exists(other):
        ocfg = {"pref": "tokyo", "park_id": 43295,
                "name": "ハーモニーレジデンス羽田ウエスト駐車場"}
        with open(other, encoding="utf-8") as f:
            ohtml = f.read()
        eq("別の駐車場も14行", len(bt.discover(ohtml, ocfg, None)), 14)
        eq("別の駐車場: 8/22 は満車",
           bt.parse(ohtml, ocfg, "2026-08-22")["8/22"]["status"], "full")
        got = bt.parse(ohtml, ocfg, "2026-08-21")["8/21"]
        eq("別の駐車場: 8/21 は空きあり", got["status"], "available")
        check("駐車場名が表示に入る", "ハーモニーレジデンス" in got["name"])

    print("\n  -- 異常系（合成HTML） --")
    eq("合成: 8/22 は満車", bt_parse("btimes_ok.html", "2026-08-22")["8/22"]["status"],
       "full")
    eq("合成: 8/18 は空きあり",
       bt_parse("btimes_ok.html", "2026-08-18")["8/18"]["status"], "available")

    for fixture, date, fragment, desc in [
        ("btimes_unknown_status.html", "2026-08-22", "判定できません", "未知のstatus classは失敗"),
        ("btimes_no_date.html", "2026-08-22", "日付を取得できません", "日付属性が無ければ失敗"),
        ("btimes_label_mismatch.html", "2026-08-22", "食い違って", "表示日付との食い違いを検出"),
        ("btimes_empty.html", "2026-08-22", "見つかりません", "行が無ければ失敗"),
        ("btimes_out_of_range.html", "2026-08-22", "見つかりません", "範囲外は既定で失敗"),
    ]:
        try:
            bt_parse(fixture, date)
            check(desc, False)
        except Exception as exc:
            check(f"{desc}（{type(exc).__name__}）", fragment in str(exc))

    # 範囲外を許容する設定なら、受付不可として正常に返す
    out = bt_parse("btimes_out_of_range.html", "2026-08-22", allow_out_of_range=True)
    eq("範囲外を許容すると closed で正常終了", out["8/22"]["status"], "closed")
    eq("範囲外の目印を残す", out["8/22"]["raw"], "out-of-range")
    check("範囲外だと分かる表示名", "表示範囲外" in out["8/22"]["name"])

    # tr の class とバッジが食い違ったら、バッジを採用しつつ警告する
    warned = []
    with open(os.path.join(FIX, "btimes_class_conflict.html"), encoding="utf-8") as f:
        got = bt.parse(f.read(), bcfg, "2026-08-22", warn=warned.append)
    eq("バッジ側を採用する", got["8/22"]["status"], "available")
    check("食い違いを警告する", any("食い違" in w for w in warned))

    # 行番号で引いていないこと: 窓がずれても日付で正しく引ける
    shifted = bt_parse("btimes_out_of_range.html", "2026-08-05")
    eq("窓がずれても日付で引ける", shifted["8/5"]["status"], "full")

    shutil.rmtree(tmp)
    print("\n" + "=" * 30)
    print(f"  成功 {PASS} / 失敗 {FAIL}")
    print("=" * 30)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
