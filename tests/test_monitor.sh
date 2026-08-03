#!/usr/bin/env bash
# monitor.py の回帰テスト。
#
# 【重要】入力は tests/fixtures/ の合成HTML（仕様から組み立てたもので実サイトのものではない）。
# パース・状態遷移・エラー処理が動くことの確認であり、
# 仕様が実サイトと一致しているかの検証ではない。
#
# 実行: bash tests/test_monitor.sh

set -u
cd "$(dirname "$0")/.."

FIX=tests/fixtures
TMP=$(mktemp -d)
STATE="$TMP/state.json"
PASS=0
FAIL=0

# 通知経路を「NTFY_TOPIC 未設定 → exit 1」に固定する
unset NTFY_TOPIC

python3 tests/make_fixture.py >/dev/null

run() {  # run <fixture> [extra args...]
  local f=$1; shift
  OUT=$(python3 monitor.py --file "$FIX/$f" --state "$STATE" --write-state-from-file "$@" 2>&1)
  CODE=$?
}

check() {  # check <説明> <期待exit> <実際exit>
  if [ "$2" = "$3" ]; then
    echo "  ok   : $1 (exit=$3)"
    PASS=$((PASS + 1))
  else
    echo "  FAIL : $1 (期待 exit=$2, 実際 exit=$3)"
    echo "$OUT" | sed 's/^/         /'
    FAIL=$((FAIL + 1))
  fi
}

contains() {  # contains <説明> <文字列>
  if echo "$OUT" | grep -qF "$2"; then
    echo "  ok   : $1"
    PASS=$((PASS + 1))
  else
    echo "  FAIL : $1 — 出力に '$2' が含まれません"
    echo "$OUT" | sed 's/^/         /'
    FAIL=$((FAIL + 1))
  fi
}

state_status() {  # state_status <F1..F4>
  python3 -c "import json,sys; print(json.load(open('$STATE'))['rooms']['$1']['status'])" 2>/dev/null
}

echo "== 1. 全サイト空きなし（実測値どおりか） =="
rm -f "$STATE"
run all_full.html
check "exit 0" 0 $CODE
for k in F1 F2 F3 F4; do contains "$k が空きなし判定" "$k  空きなし"; done
contains "通知しない" "通知しません"
[ "$(state_status F1)" = "full" ] && { echo "  ok   : state に full が保存された"; PASS=$((PASS+1)); } \
  || { echo "  FAIL : state が full でない"; FAIL=$((FAIL+1)); }

echo
echo "== 2. F2/F3 が空きあり → 通知 =="
run f2f3_available.html
check "NTFY_TOPIC 未設定なので exit 1" 1 $CODE
contains "F2 を通知" "・F2"
contains "F3 を通知" "・F3"
contains "予約URLを含む" "date=2026-09-19"
if echo "$OUT" | grep -qF "・F1"; then
  echo "  FAIL : 空きなしの F1 が通知に含まれている"; FAIL=$((FAIL+1))
else
  echo "  ok   : 空きなしの F1 は通知に含まれない"; PASS=$((PASS+1))
fi

echo
echo "== 3. 同じ状態で再実行 → 再通知しない =="
run f2f3_available.html
check "exit 0（再通知なし）" 0 $CODE
contains "通知しない" "通知しません"

echo
echo "== 4. 満室に戻る =="
run all_full.html
check "exit 0" 0 $CODE
[ "$(state_status F2)" = "full" ] && { echo "  ok   : F2 が full に戻った"; PASS=$((PASS+1)); } \
  || { echo "  FAIL : F2 が full に戻っていない"; FAIL=$((FAIL+1)); }

echo
echo "== 5. 再び空きあり → 再通知される =="
run f2f3_available.html
check "exit 1（再通知）" 1 $CODE

echo
echo "== 6. 表示期間がずれても 9/19 を正しく探せる =="
rm -f "$STATE"
run shifted.html
check "exit 0" 0 $CODE
contains "9/19 は列2にある" "列=2"
contains "空きなしと判定" "F1  空きなし"

echo
echo "== 7. 異常系: state.json を更新しないこと =="
rm -f "$STATE"
run all_full.html                     # 正常な state を作る
BEFORE=$(cat "$STATE")

for f in date_missing.html room_missing.html unknown_icon.html; do
  run "$f"
  check "$f は exit 2" 2 $CODE
  contains "$f: state 未更新と表示" "state.json は更新していません"
  if [ "$(cat "$STATE")" = "$BEFORE" ]; then
    echo "  ok   : $f で state.json が変更されていない"; PASS=$((PASS+1))
  else
    echo "  FAIL : $f で state.json が変更された"; FAIL=$((FAIL+1))
  fi
done

echo
echo "== 8. 取得失敗時も state.json を更新しない =="
OUT=$(python3 monitor.py --file "$FIX/does_not_exist.html" --state "$STATE" --write-state-from-file 2>&1); CODE=$?
check "存在しないファイルは exit 2" 2 $CODE
[ "$(cat "$STATE")" = "$BEFORE" ] && { echo "  ok   : state.json 未変更"; PASS=$((PASS+1)); } \
  || { echo "  FAIL : state.json が変更された"; FAIL=$((FAIL+1)); }

echo
echo "== 9. --test-notify は強制発火し state を更新しない =="
BEFORE=$(cat "$STATE")
OUT=$(python3 monitor.py --file "$FIX/all_full.html" --state "$STATE" --test-notify 2>&1); CODE=$?
check "exit 1（NTFY_TOPIC 未設定）" 1 $CODE
contains "満室でも全サイトを通知" "・F4"
[ "$(cat "$STATE")" = "$BEFORE" ] && { echo "  ok   : state.json 未変更"; PASS=$((PASS+1)); } \
  || { echo "  FAIL : state.json が変更された"; FAIL=$((FAIL+1)); }

echo
echo "== 10. --file 単体では state を更新しない（既定動作） =="
rm -f "$STATE"
OUT=$(python3 monitor.py --file "$FIX/all_full.html" --state "$STATE" 2>&1); CODE=$?
check "exit 0" 0 $CODE
[ ! -f "$STATE" ] && { echo "  ok   : state.json が作られない"; PASS=$((PASS+1)); } \
  || { echo "  FAIL : state.json が作られた"; FAIL=$((FAIL+1)); }

rm -rf "$TMP"
echo
echo "=============================="
echo "  成功 $PASS / 失敗 $FAIL"
echo "=============================="
[ "$FAIL" -eq 0 ] || exit 1
