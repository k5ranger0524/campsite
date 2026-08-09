#!/usr/bin/env bash
#
# scripts/push-state.sh の確認。
# 本物の git リポジトリを一時ディレクトリに作り、「別の実行が先に push した」
# 状況を再現して、後勝ちで解決できることを確かめる。
#
#   bash tests/test_push_state.sh

set -u

script=$(cd "$(dirname "$0")/.." && pwd)/scripts/push-state.sh
branch=work
ok=0
ng=0

pass() { echo "  ok   : $1"; ok=$((ok + 1)); }
fail() { echo "  NG   : $1"; echo "         $2"; ng=$((ng + 1)); }

check() {  # 説明 期待 実際
  if [ "$2" = "$3" ]; then pass "$1"; else fail "$1" "期待=$2 実際=$3"; fi
}

# 素の環境で動くよう、利用者の git 設定に依存しない
export GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_SYSTEM=/dev/null
export GIT_AUTHOR_NAME=t GIT_AUTHOR_EMAIL=t@example.com
export GIT_COMMITTER_NAME=t GIT_COMMITTER_EMAIL=t@example.com

setup() {  # 中央リポジトリと作業用クローンを作る
  root=$(mktemp -d)
  git init -q --bare "$root/origin.git"
  git clone -q "$root/origin.git" "$root/work" 2>/dev/null
  cd "$root/work"
  git checkout -q -b "$branch"
  echo '{"targets":{}}' > state.json
  git add state.json
  git commit -q -m init
  git push -q origin "$branch"
  git branch -q --set-upstream-to="origin/$branch" 2>/dev/null || true
}

teardown() { cd /; rm -rf "$root"; }

remote_state() {  # 中央リポジトリに入っている内容
  git -C "$root/origin.git" show "$branch:state.json"
}

# 別の実行が先に push した状況を作る
other_run_pushes() {  # $1 = その実行が書いた内容
  git clone -q "$root/origin.git" "$root/other"
  git -C "$root/other" checkout -q "$branch"
  printf '%s\n' "$1" > "$root/other/state.json"
  git -C "$root/other" add state.json
  git -C "$root/other" commit -q -m "other"
  git -C "$root/other" push -q origin "$branch"
  rm -rf "$root/other"
}

echo "== 1. 変化がなければコミットしない =="
setup
before=$(git rev-parse HEAD)
out=$(bash "$script" state.json "$branch" 2>&1); rc=$?
check "終了コード0" 0 "$rc"
check "コミットされない" "$before" "$(git rev-parse HEAD)"
case "$out" in *変化なし*) pass "変化なしと報告する";; *) fail "変化なしと報告する" "$out";; esac
teardown

echo
echo "== 2. 競合がなければそのまま push できる =="
setup
echo '{"targets":{"a":1}}' > state.json
out=$(bash "$script" state.json "$branch" 2>&1); rc=$?
check "終了コード0" 0 "$rc"
check "中央に反映される" '{"targets":{"a":1}}' "$(remote_state)"
teardown

echo
echo "== 3. 別の実行が同じ箇所を書き換えていても解決できる（本題）=="
# これが今回ジョブを落とした状況。rebase だと CONFLICT で止まっていた。
setup
echo '{"targets":{"P2":1,"P3":1,"P4":1}}' > state.json   # 今回の実行が読んだ内容
git add state.json && git commit -q -m base && git push -q origin "$branch"
other_run_pushes '{"targets":{"P2":9,"P3":9,"P4":9}}'    # 別の実行が先に push
echo '{"targets":{"P2":2}}' > state.json                 # 今回の観測（対象を減らした）
out=$(bash "$script" state.json "$branch" 2>&1); rc=$?
check "終了コード0（ジョブを落とさない）" 0 "$rc"
check "今回の観測が採用される" '{"targets":{"P2":2}}' "$(remote_state)"
case "$out" in *弾かれました*) pass "取り込み直したと報告する";; *) fail "取り込み直したと報告する" "$out";; esac
check "rebase が途中で止まっていない" "" "$(git status --porcelain | grep -c '^UU' | tr -d ' 0')"
teardown

echo
echo "== 4. 別の実行が同じ内容を push 済みなら何もしない =="
setup
other_run_pushes '{"targets":{"P2":2}}'
echo '{"targets":{"P2":2}}' > state.json
out=$(bash "$script" state.json "$branch" 2>&1); rc=$?
check "終了コード0" 0 "$rc"
check "中央の内容は変わらない" '{"targets":{"P2":2}}' "$(remote_state)"
case "$out" in *同じ内容*) pass "同じ内容と報告する";; *) fail "同じ内容と報告する" "$out";; esac
teardown

echo
echo "== 5. push 先が無ければ失敗する（異常を握りつぶさない）=="
setup
echo '{"targets":{"a":1}}' > state.json
git remote set-url origin "$root/does-not-exist.git"
out=$(bash "$script" state.json "$branch" 2>&1); rc=$?
check "終了コード1" 1 "$rc"
teardown

echo
echo "=============================="
echo "  成功 $ok / 失敗 $ng"
echo "=============================="
[ "$ng" -eq 0 ]
