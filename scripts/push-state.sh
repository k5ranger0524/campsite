#!/usr/bin/env bash
#
# 生成した state ファイルをブランチに反映する。GitHub Actions から呼ぶ。
#
# 使い方: scripts/push-state.sh <stateファイル> <ブランチ名> [コミットメッセージ]
#
# なぜ rebase を使わないか:
#   state は「最後に確認した結果」だけを持つ生成物で、2つの実行の内容を
#   行単位で合流させても意味がない。実際 `git push || (git pull --rebase && git push)`
#   では、監視対象を変えた直後に同じ箇所を書き換えて衝突し、rebase が途中で
#   止まったまま exit 1 になった（＝空きは出ていないのに「監視が壊れた」通知）。
#   そこで衝突を解決しようとせず、最新の remote に載せ替えたうえで
#   今回観測した内容で上書きする（後勝ち）。
#
# 後勝ちで問題ない理由:
#   同じ state ファイルを書くのは同じワークフローだけで、concurrency により
#   同時には走らない。よって「今回の観測」が常に最新である。
#
# 注意: CI 専用。作業ツリーを reset --hard で捨てるので、手元では実行しないこと。

set -u

file=${1:?stateファイルを指定してください}
branch=${2:?ブランチ名を指定してください}
message=${3:-update state}

changed() { [ -n "$(git status --porcelain -- "$file")" ]; }

if ! changed; then
  echo "変化なし: $file（コミットしません）"
  exit 0
fi

git config user.name  github-actions
git config user.email github-actions@github.com

# 載せ替えのたびに書き戻せるよう、今回の内容を退避しておく
mine=$(mktemp)
trap 'rm -f "$mine"' EXIT
cp "$file" "$mine"

for attempt in 1 2 3 4 5; do
  git add -- "$file"
  git commit -q -m "$message" -- "$file" || true

  if git push -q origin "HEAD:$branch"; then
    echo "state を push しました: $file（$attempt 回目）"
    exit 0
  fi

  echo "push が弾かれました。最新を取り込み直します（$attempt 回目）"
  git fetch -q origin "$branch" || true
  git reset -q --hard "origin/$branch"
  cp "$mine" "$file"

  if ! changed; then
    echo "他の実行が同じ内容を push 済み: $file（何もしません）"
    exit 0
  fi

  sleep "$attempt"
done

echo "state を push できませんでした: $file" >&2
exit 1
