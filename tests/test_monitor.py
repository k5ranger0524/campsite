#!/usr/bin/env python3
"""monitor.py の回帰テスト。

【重要】入力は tests/fixtures/ の合成HTML（仕様から組み立てたもので実サイトのものではない）。
パース・通知判定・状態遷移・エラー処理が動くことの確認であり、
仕様が実サイトと一致しているかの検証ではない。

ntfy.sh への送信は monkeypatch で差し替えて検証する（実送信はしない）。

実行: python3 tests/test_monitor.py
"""

import json
import os
import shutil
import sys
import tempfile
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import monitor  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
FIX = os.path.join(HERE, "fixtures")

PASS = 0
FAIL = 0
SENT = []          # 送信された通知を記録する
SEND_SHOULD_FAIL = False


def fake_send_ntfy(topic, title, message, url):
    if SEND_SHOULD_FAIL:
        raise monitor.requests.RequestException("接続失敗(テスト)")
    SENT.append({"topic": topic, "title": title, "message": message, "url": url})


monitor.send_ntfy = fake_send_ntfy


def check(desc, cond):
    global PASS, FAIL
    if cond:
        print(f"  ok   : {desc}")
        PASS += 1
    else:
        print(f"  FAIL : {desc}")
        FAIL += 1


def eq(desc, actual, expected):
    check(f"{desc} (期待={expected!r}, 実際={actual!r})" if actual != expected else desc,
          actual == expected)


def run(fixture, state, *extra, topic="test-topic"):
    """monitor を1回実行し、(exit_code, 送信された通知) を返す。"""
    SENT.clear()
    argv = ["monitor.py", "--state", state]
    if fixture:
        argv += ["--file", os.path.join(FIX, fixture), "--write-state-from-file"]
    argv += list(extra)

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


def backdate(path, key, hours):
    """指定サイトの最終通知時刻を hours 時間前に巻き戻す（時間経過のシミュレーション）。"""
    data = read_state(path)
    t = datetime.fromisoformat(data["rooms"][key]["notified_at"])
    data["rooms"][key]["notified_at"] = (t - timedelta(hours=hours)).isoformat()
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def main():
    global SEND_SHOULD_FAIL

    sys.path.insert(0, HERE)
    import make_fixture
    make_fixture.main()
    print()

    tmp = tempfile.mkdtemp()
    state = os.path.join(tmp, "state.json")

    print("== 1. 全サイト空きなし（実測値どおりか） ==")
    code, sent = run("all_full.html", state)
    eq("exit 0", code, 0)
    eq("通知なし", len(sent), 0)
    st = read_state(state)
    for k in ["F1", "F2", "F3", "F4"]:
        eq(f"{k} が full", st["rooms"][k]["status"], "full")
    eq("notified_at は None", st["rooms"]["F1"]["notified_at"], None)

    print("\n== 2. F2/F3 が空きあり → 新規通知（exit 0） ==")
    code, sent = run("f2f3_available.html", state)
    eq("通知しても exit 0", code, 0)
    eq("1件送信", len(sent), 1)
    msg = sent[0]["message"]
    eq("タイトルは新規通知", sent[0]["title"], "赤城山オートキャンプ場 空き通知")
    check("F2 を含む", "・F2" in msg)
    check("F3 を含む", "・F3" in msg)
    check("空きなしの F1 を含まない", "・F1" not in msg)
    check("予約URLを含む", "date=2026-09-19" in msg)
    check("新規なので継続中の文言は無い", "継続中" not in msg)
    st = read_state(state)
    check("F2 に notified_at が記録された", st["rooms"]["F2"]["notified_at"] is not None)
    eq("F1 は空きなしなので None", st["rooms"]["F1"]["notified_at"], None)

    print("\n== 3. 2時間以内の再実行 → 再通知しない ==")
    code, sent = run("f2f3_available.html", state)
    eq("exit 0", code, 0)
    eq("通知なし", len(sent), 0)

    print("\n== 4. 2時間経過 → 継続中として再通知 ==")
    backdate(state, "F2", 3)
    backdate(state, "F3", 3)
    code, sent = run("f2f3_available.html", state)
    eq("exit 0", code, 0)
    eq("1件送信", len(sent), 1)
    msg = sent[0]["message"]
    eq("タイトルは継続中", sent[0]["title"], "赤城山オートキャンプ場 空き継続中")
    check("本文に『継続中』が入る", "継続中" in msg)
    check("F2 が継続中として載る", "・F2  【F2】3家族サイト（継続中）" in msg)
    check("再通知間隔の説明が入る", "2 時間ごと" in msg)

    print("\n== 5. 片方だけ2時間経過 → そのサイトだけ再通知 ==")
    backdate(state, "F3", 3)
    code, sent = run("f2f3_available.html", state)
    eq("1件送信", len(sent), 1)
    msg = sent[0]["message"]
    check("F3 は載る", "・F3" in msg)
    check("F2 は載らない", "・F2" not in msg)

    print("\n== 6. 満室に戻ると通知履歴がリセットされる ==")
    code, sent = run("all_full.html", state)
    eq("exit 0", code, 0)
    eq("通知なし", len(sent), 0)
    st = read_state(state)
    eq("F2 の notified_at がリセット", st["rooms"]["F2"]["notified_at"], None)
    eq("F3 の notified_at がリセット", st["rooms"]["F3"]["notified_at"], None)

    print("\n== 7. 再び空いたら『新規』として通知される ==")
    code, sent = run("f2f3_available.html", state)
    eq("1件送信", len(sent), 1)
    eq("タイトルは新規通知", sent[0]["title"], "赤城山オートキャンプ場 空き通知")
    check("継続中ではない", "継続中" not in sent[0]["message"])

    print("\n== 8. 表示期間がずれても 9/19 を正しく探せる ==")
    s2 = os.path.join(tmp, "shifted.json")
    code, sent = run("shifted.html", s2)
    eq("exit 0", code, 0)
    st = read_state(s2)
    # 9/19 の列(=列2)だけ phone にしてあるので、この値が出れば列を正しく引けている
    eq("列2(9/19)を読んでいる", st["rooms"]["F1"]["status"], "phone")

    print("\n== 8b. 先頭列以外の日付も引ける（月付き表記が無い列） ==")
    s2b = os.path.join(tmp, "col.json")
    code, sent = run("all_full.html", s2b, "--date", "2026-09-21")
    eq("9/21 は exit 0", code, 0)
    eq("9/21 は空きあり", read_state(s2b)["rooms"]["F1"]["status"], "available")

    s2c = os.path.join(tmp, "col2.json")
    code, sent = run("all_full.html", s2c, "--date", "2026-10-02")
    eq("月跨ぎの 10/2 も exit 0", code, 0)
    eq("10/2 は空きあり", read_state(s2c)["rooms"]["F1"]["status"], "available")

    s2d = os.path.join(tmp, "col3.json")
    code, sent = run("all_full.html", s2d, "--date", "2026-10-03")
    eq("表示期間外は exit 1", code, 1)
    check("state.json を作らない", not os.path.exists(s2d))

    print("\n== 9. NTFY_TOPIC 未設定は起動直後に exit 1 ==")
    s3 = os.path.join(tmp, "cfg.json")
    code, sent = run(None, s3, "--date", "2026-09-19", topic=None)
    eq("exit 1", code, 1)
    check("state.json を作らない", not os.path.exists(s3))
    code, sent = run("all_full.html", s3, "--test-notify", topic=None)
    eq("--test-notify でも exit 1", code, 1)

    print("\n== 10. 異常系は exit 1 かつ state.json を更新しない ==")
    code, _ = run("all_full.html", state)      # 正常な state を作り直す
    before = open(state, encoding="utf-8").read()
    for fx in ["date_missing.html", "room_missing.html", "unknown_icon.html"]:
        code, sent = run(fx, state)
        eq(f"{fx} は exit 1", code, 1)
        eq(f"{fx}: 通知しない", len(sent), 0)
        check(f"{fx}: state.json 未変更",
              open(state, encoding="utf-8").read() == before)

    code, sent = run("does_not_exist.html", state)
    eq("存在しないファイルは exit 1", code, 1)
    check("state.json 未変更", open(state, encoding="utf-8").read() == before)

    # ヘッダの日付が飛んでいる場合、オフセットで求めた列が目的日と食い違う。
    # 先頭列は正しいままなので、先頭列以外を狙って初めて検出できる。
    code, sent = run("header_drift.html", state, "--date", "2026-09-21")
    eq("ヘッダ日付のずれを検出して exit 1", code, 1)
    check("state.json 未変更", open(state, encoding="utf-8").read() == before)

    print("\n== 11. ntfy 送信失敗は exit 1 かつ state.json を更新しない ==")
    SEND_SHOULD_FAIL = True
    code, sent = run("f2f3_available.html", state)
    SEND_SHOULD_FAIL = False
    eq("exit 1", code, 1)
    check("state.json 未変更（次回また通知を試みる）",
          open(state, encoding="utf-8").read() == before)

    print("\n== 12. 送信失敗の次の回は通知をやり直す ==")
    code, sent = run("f2f3_available.html", state)
    eq("exit 0", code, 0)
    eq("1件送信", len(sent), 1)

    print("\n== 13. --test-notify は強制発火し state を更新しない ==")
    before = open(state, encoding="utf-8").read()
    code, sent = run("all_full.html", state, "--test-notify")
    eq("exit 0", code, 0)
    eq("1件送信", len(sent), 1)
    msg = sent[0]["message"]
    check("満室でも全サイトが載る", "・F4" in msg)
    check("state.json 未変更", open(state, encoding="utf-8").read() == before)

    # 本物の空き通知と見分けられないと困るので、文面を固定する
    check("タイトルに【テスト】が付く", sent[0]["title"].startswith("【テスト】"))
    check("テストである旨を明記", "これは通知テストです" in msg)
    check("『空きが出ました』とは書かない", "空きが出ました" not in msg)
    check("『継続中』とも書かない", "継続中" not in msg)
    check("実際の判定結果（空きなし）を載せる", "・F1  空きなし" in msg)

    # 空きがある状態でテストしても、空き通知の文面にはならない
    code, sent = run("f2f3_available.html", state, "--test-notify")
    msg = sent[0]["message"]
    check("空きありでも『空きが出ました』と書かない", "空きが出ました" not in msg)
    check("F2 は空きありと表示", "・F2  空きあり" in msg)
    check("F1 は空きなしと表示", "・F1  空きなし" in msg)

    print("\n== 14. --file 単体では state を更新しない（既定動作） ==")
    s4 = os.path.join(tmp, "nofile.json")
    SENT.clear()
    sys.argv = ["monitor.py", "--file", os.path.join(FIX, "all_full.html"), "--state", s4]
    os.environ["NTFY_TOPIC"] = "test-topic"
    eq("exit 0", monitor.main(), 0)
    check("state.json が作られない", not os.path.exists(s4))

    print("\n== 15. --repeat-hours で間隔を変更できる ==")
    s5 = os.path.join(tmp, "rep.json")
    run("f2f3_available.html", s5)                       # 新規通知
    backdate(s5, "F2", 0.75)                             # 45分経過
    code, sent = run("f2f3_available.html", s5, "--repeat-hours", "0.5")
    eq("0.5時間設定なら再通知される", len(sent), 1)
    check("F2 が継続中", "・F2" in sent[0]["message"])

    shutil.rmtree(tmp)

    print("\n" + "=" * 30)
    print(f"  成功 {PASS} / 失敗 {FAIL}")
    print("=" * 30)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
