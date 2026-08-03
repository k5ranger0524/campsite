# 赤城山オートキャンプ場 予約サイト 調査フェーズ

対象URL:
`https://reserve.489ban.net/client/autocamp-akagi/0/plan/availability/room/stay?date=2026-09-19`

目的: 空き/満室がHTML上でどう表現されているかを特定し、requestsだけで判定可能かを検証する。

---

## 検証済み（このサンドボックスで実際に確認した事実）

### 1. このサンドボックスからは対象サイトに一切アクセスできない

`requests` での取得は、サイトからの応答ではなく **エージェントプロキシの段階** で失敗した。

```
ProxyError: Tunnel connection failed: 403 Forbidden
  host: reserve.489ban.net:443
  detail: "gateway answered 403 to CONNECT (policy denial or upstream failure)"
```

これはサイト固有のブロック（bot対策など）**ではない**。同じ403が全ホストで発生する:

| ホスト | 結果 |
|---|---|
| `reserve.489ban.net` | 403 (CONNECT拒否) |
| `489ban.net` | 403 (CONNECT拒否) |
| `example.com` | 403 (CONNECT拒否) |
| `www.google.com` | 403 (CONNECT拒否) |

サーバ側フェッチャ（WebFetch）も同様に 403。
→ **実行環境のegressポリシーによる全外部通信の遮断**であり、サイトの挙動とは無関係。

プロキシのREADME (`/root/.ccr/README.md`) はポリシー拒否について
「リトライも迂回もせず報告せよ」と明記しているため、回避は行っていない。

### 2. 結果として、調査項目 2〜4 は未実施

`debug.html` を取得できていないため、以下は**一切確認できていない**:

- F1〜F4 の行が生HTMLに存在するか
- 9/19セルが img / class / alt / テキストのどれで表現されているか
- 空きと満室がHTML上でどう区別されているか

これらについて、この時点で言えることは何もない。推測は記載しない。

### 3. 調査用スクリプトは作成・動作確認済み

ネットワークさえ通れば、上記2〜4を機械的に確定させられるスクリプトを用意した。
合成HTML（`rowspan`/`colspan` 混在、img型・文字型の両方を含む）で動作確認済み。

**注意: 動作確認に使った合成HTMLは私が作った架空のものであり、
実サイトの構造を示すものでは一切ない。パーサが動くことの確認にすぎない。**

---

## 未検証（あなたの手元で実行して確定させること）

ネットワークの通る環境で以下を実行してください。

```bash
cd research
pip install -r requirements.txt
python3 fetch_debug.py          # debug.html を保存
python3 analyze_debug.py        # 証拠を出力
```

### 出力の読み方（これで調査項目2〜4が確定する）

| 出力セクション | 何が分かるか |
|---|---|
| **A. ページの素性** | 生HTMLに `F1`〜`F4` の文字列があるか。JSフレームワークの痕跡。**ここで `F1` が0箇所ならJS描画確定** |
| **B. F1〜F4の行** | 各行の各セルの**生HTMLをそのまま**表示。9/19が何列目かはヘッダ推定付き（`rowspan`/`colspan`考慮済み） |
| **C. セル表現インベントリ** | 全セルを「img src / alt / class / style / リンク有無 / テキスト」で分類し出現回数順に列挙。**空きと満室の差分がここに現れる** |
| **D. img/alt 集計** | ○×アイコンのファイル名とalt値の一覧。記号文字（○×）の出現数も集計 |

判定に使えるのが `src` なのか `alt` なのか `class` なのかは、
C と D の出力を見れば**推測なしで確定できる**。

### 想定される2つの結末

**(a) B に F1〜F4 の行が出て、C に2種類以上のセル表現が出た場合**
→ requests + BeautifulSoup で判定可能。C の差分をそのまま判定条件にする。

**(b) B が空、かつ A で `F1` が 0 箇所だった場合**
→ 表はJavaScriptで後から描画されている。requestsでは原理的に取得不能。
**Playwright でのレンダリングが必要**になる:

```bash
pip install playwright && playwright install chromium
```

その場合、`page.goto(url)` 後に表のセレクタを `wait_for_selector` で待ってから
`page.content()` を取り、同じ `analyze_debug.py` にかければ以降の解析は流用できる。

なお (b) の場合、ページが裏で叩いているJSON APIを直接叩ける可能性もある
（DevToolsのNetworkタブで確認）。その方が監視ツールとしては軽量で安定する。

---

## ファイル

| ファイル | 役割 |
|---|---|
| `fetch_debug.py` | 生HTML取得。ブラウザUA設定、セッション確立、指数バックオフ再試行。**判定ロジックは持たない** |
| `analyze_debug.py` | debug.html を解析し証拠を出力。**「○なら空き」等の前提を一切置かない** |
| `requirements.txt` | 依存 |

監視ツール本体は未実装（調査フェーズのため）。
