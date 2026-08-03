# 赤城山オートキャンプ場 空き監視

【F1】〜【F4】(3家族サイト) の **2026/9/19(土)** に空きが出たら通知する。

「空きなし」→「空きあり」に変化したときだけ通知し、
一度通知したら、また「空きなし」に戻るまで再通知しない。

---

## セットアップ

```bash
pip install -r requirements.txt
```

## 使い方

```bash
python3 monitor.py                        # 実サイトを監視
python3 monitor.py --date 2026-09-20      # 別の日を監視
python3 monitor.py --file debug.html      # ローカルHTMLでパースをテスト
python3 monitor.py --test-notify          # 通知処理だけを強制発火
```

### オプション

| オプション | 意味 |
|---|---|
| `--date YYYY-MM-DD` | 監視対象日（既定 `2026-09-19`） |
| `--file PATH` | 実サイトの代わりにローカルHTMLを読む。既定では `state.json` を更新しない |
| `--state PATH` | 状態ファイル（既定 `state.json`） |
| `--test-notify` | 状態に関わらず通知を強制発火。`state.json` は更新しない |
| `--write-state-from-file` | `--file` でも `state.json` を更新する（テスト用） |

### 終了コード

| コード | 意味 |
|---|---|
| `0` | 正常終了（変化なし、または ntfy 送信成功） |
| `1` | **空きを検知したが `NTFY_TOPIC` 未設定** → ジョブ失敗として通知する |
| `2` | 異常終了（取得失敗・構造変化など）。`state.json` は更新しない |

## 通知

`NTFY_TOPIC` の有無で経路が切り替わる。どちらでも使える。

**(a) `NTFY_TOPIC` を設定した場合** — ntfy.sh に送信して `exit 0`。

```bash
export NTFY_TOPIC=your-secret-topic-name
python3 monitor.py
```

スマホの [ntfy アプリ](https://ntfy.sh/) で同じトピック名を購読しておく。
トピック名は URL を知っていれば誰でも購読できるため、推測されにくい文字列にすること。

**(b) `NTFY_TOPIC` 未設定の場合** — `exit 1` で終了する。
GitHub Actions ではジョブ失敗となり、GitHub からメールが届く。

通知本文には空いたサイト名（`【F2】3家族サイト` など）と予約ページURLが入る。

> 補足: (b) の経路でも「通知した」とみなして `state.json` を更新する。
> 仕様どおり再通知はしないので、**ジョブ失敗メールを見逃すと次の通知は
> 一度満室に戻るまで来ない**。確実に受け取りたい場合は (a) を推奨。

## エラー時の動作

次の場合は `exit 2` で終了し、**`state.json` を更新しない**。

- ページ取得に失敗した（4回まで指数バックオフで再試行したうえで失敗）
- F1〜F4 の 4 つすべてを見つけられなかった
- 9/19 の列が表の中に見つからなかった
- `<i>` に既知のアイコン class がなかった
- th と td の個数が一致しなかった
- ntfy.sh への送信に失敗した

失敗を「空きなし」と誤記録して次回に誤通知するのを防ぐため。
またサイト側のHTML構造が変わった場合も、黙って誤判定せずジョブ失敗として気づけるようにしている。

## 判定ロジック

| 対象 | 方法 |
|---|---|
| サイトの特定 | `li[id="room_24246"]` 〜 `room_24249` |
| サイト名 | `li` 内の `dl.webc_avlbl_item dt` |
| 日付列 | `div.webc_avlbl_cal table` の `thead th` を走査し、`span[0]` が `9/19` の列を探す（**インデックス決め打ちはしない**） |
| 空き状況 | 同じインデックスの `tbody td` 内の `<i>` の class |

| class | 判定 |
|---|---|
| `fa-circle` | 空きあり ← 通知対象 |
| `fa-xmark` | 空きなし |
| `fa-square-phone` | 電話問い合わせ |
| `fa-minus` | 受付できません |

補助チェックとして「空きありの td のみリンクを持つ」も検査し、
食い違ったら警告を出す（判定自体は `<i>` の class を優先）。

## テスト

```bash
bash tests/test_monitor.sh
```

パース・状態遷移・エラー処理を 36 項目で検証する。

**注意:** `tests/fixtures/` のHTMLは仕様から組み立てた**合成データ**であり、
実サイトから取得したものではない（`tests/make_fixture.py` で生成）。
コードが仕様どおり動くことの確認であって、仕様が実サイトと一致することの証明ではない。

実サイトの `debug.html` が手に入ったら、それで確認するのが確実:

```bash
python3 monitor.py --file debug.html    # F1〜F4 すべて「空きなし」になるはず
```

## GitHub Actions での定期実行

`state.json` を実行間で引き継ぐ必要がある点に注意。
リポジトリにコミットして持ち回るのが一番単純。

```yaml
name: akagi-monitor
on:
  schedule:
    - cron: '*/30 * * * *'   # 30分ごと (UTC)
  workflow_dispatch:

permissions:
  contents: write

jobs:
  check:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.12'
      - run: pip install -r requirements.txt

      - name: 空き状況をチェック
        env:
          NTFY_TOPIC: ${{ secrets.NTFY_TOPIC }}   # 未設定ならジョブ失敗で通知
        run: python3 monitor.py

      - name: state.json を保存
        if: always()          # 通知でexit 1になっても保存する
        run: |
          if [ -n "$(git status --porcelain state.json)" ]; then
            git config user.name  github-actions
            git config user.email github-actions@github.com
            git add state.json
            git commit -m "update state"
            git push
          fi
```

`if: always()` が必要な理由: `NTFY_TOPIC` 未設定で通知が起きると `exit 1` になるため、
これがないと `state.json` が保存されず毎回同じ通知が繰り返される。

`exit 2`（異常終了）のときは `monitor.py` 自身が `state.json` を書き換えないので、
`always()` でも誤った状態がコミットされることはない。
