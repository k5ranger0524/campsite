#!/usr/bin/env python3
"""debug.html を解析し、空き/満の表現がHTML上でどう区別されているかを「証拠として」出力する。

このスクリプトは判定ロジックを持たない。
「○なら空き」といった前提を一切置かず、HTMLに実在する表現を列挙・比較して出すだけ。
どれが空きでどれが満室かは、出力とブラウザ表示を突き合わせて人間が確定させる。

使い方:
    python3 analyze_debug.py [--file debug.html] [--full]
"""

import argparse
import collections
import os
import re
import sys

from bs4 import BeautifulSoup

FAMILY_RE = re.compile(r"\bF\s*([1-4])\b", re.I)
# JSレンダリングの痕跡候補
JS_MARKERS = [
    "__NEXT_DATA__", "ReactDOM", "react-dom", "data-reactroot", "_app-",
    "Vue.createApp", "new Vue", "v-if", "v-for", "data-v-",
    "ng-app", "ng-controller", "angular", "knockout", "data-bind",
    "Alpine.start", "x-data", "htmx", "Stimulus",
    "$.ajax", "fetch(", "XMLHttpRequest", "axios",
]

SEP = "=" * 78
SUB = "-" * 78


def norm(text):
    return re.sub(r"\s+", " ", (text or "")).strip()


def basename(src):
    if not src:
        return None
    return src.split("?")[0].rstrip("/").split("/")[-1] or src


def clip(s, limit, full):
    s = norm(s)
    if full or len(s) <= limit:
        return s
    return s[:limit] + f" …(+{len(s) - limit}文字)"


# --------------------------------------------------------------------------
# セルの「見た目を決めていそうな要素」をすべて拾う（何が効くかは決めつけない）
# --------------------------------------------------------------------------
def cell_signature(cell):
    imgs = []
    for img in cell.find_all("img"):
        imgs.append((
            basename(img.get("src")),
            norm(img.get("alt")),
            " ".join(img.get("class") or []),
            norm(img.get("title")),
        ))

    # 背景画像でアイコンを出すパターンもあるので style / class も拾う
    styles = [norm(t.get("style")) for t in cell.find_all(True) if t.get("style")]
    if cell.get("style"):
        styles.insert(0, norm(cell.get("style")))

    inner_classes = []
    for t in cell.find_all(True):
        if t.get("class"):
            inner_classes.append(" ".join(t.get("class")))

    links = [norm(a.get("href")) for a in cell.find_all("a")]
    inputs = [
        (i.get("type"), i.get("name"), i.get("value"), i.has_attr("disabled"))
        for i in cell.find_all(["input", "button", "select"])
    ]

    return {
        "tag": cell.name,
        "cell_class": " ".join(cell.get("class") or []),
        "imgs": tuple(imgs),
        "inner_classes": tuple(inner_classes),
        "styles": tuple(s for s in styles if "background" in s.lower() or "image" in s.lower()),
        "links": tuple(links),
        "inputs": tuple(inputs),
        "text": norm(cell.get_text()),
        "data_attrs": tuple(sorted(
            (k, norm(str(v))) for k, v in cell.attrs.items() if k.startswith("data-")
        )),
    }


def sig_key(sig):
    """出現回数を数えるためのハッシュ可能キー（テキストは含める：○×文字の可能性があるため）"""
    return (
        sig["cell_class"], sig["imgs"], sig["inner_classes"],
        sig["styles"], bool(sig["links"]), sig["inputs"], sig["text"], sig["data_attrs"],
    )


def describe_sig(sig):
    parts = []
    if sig["imgs"]:
        for src, alt, cls, title in sig["imgs"]:
            bits = [f"img src={src!r}"]
            if alt:
                bits.append(f"alt={alt!r}")
            if cls:
                bits.append(f"class={cls!r}")
            if title:
                bits.append(f"title={title!r}")
            parts.append(" ".join(bits))
    if sig["cell_class"]:
        parts.append(f"td.class={sig['cell_class']!r}")
    if sig["inner_classes"]:
        parts.append(f"内側class={list(sig['inner_classes'])}")
    if sig["styles"]:
        parts.append(f"style={list(sig['styles'])}")
    if sig["links"]:
        parts.append("aリンク有り")
    if sig["inputs"]:
        parts.append(f"input={list(sig['inputs'])}")
    if sig["data_attrs"]:
        parts.append(f"data属性={list(sig['data_attrs'])}")
    parts.append(f"text={sig['text']!r}")
    return " | ".join(parts)


# --------------------------------------------------------------------------
# rowspan/colspan を考慮して table をグリッド化する
# （単純な cells[i] のインデックス対応は結合セルでずれるため）
# --------------------------------------------------------------------------
def build_grid(table):
    grid = {}
    occupied = collections.defaultdict(set)
    rows = table.find_all("tr")
    for r, tr in enumerate(rows):
        c = 0
        for cell in tr.find_all(["td", "th"], recursive=False) or tr.find_all(["td", "th"]):
            while c in occupied[r]:
                c += 1
            try:
                cs = max(1, int(cell.get("colspan", 1)))
                rs = max(1, int(cell.get("rowspan", 1)))
            except (TypeError, ValueError):
                cs = rs = 1
            grid[(r, c)] = cell
            for dr in range(rs):
                for dc in range(cs):
                    occupied[r + dr].add(c + dc)
            c += cs
    return rows, grid


def header_texts(rows, grid):
    """先頭の方の行から、列インデックス -> ヘッダ文字列 を作る（見つからなければ空）"""
    headers = {}
    for r, tr in enumerate(rows[:4]):
        cells = [(c, cell) for (rr, c), cell in grid.items() if rr == r]
        if not cells:
            continue
        th_ratio = sum(1 for _, cell in cells if cell.name == "th") / len(cells)
        texts = {c: norm(cell.get_text()) for c, cell in cells}
        if th_ratio >= 0.5 or any(re.search(r"\d{1,2}\s*/\s*\d{1,2}|\d{1,2}日|[月火水木金土日]", t) for t in texts.values()):
            for c, t in texts.items():
                if t and c not in headers:
                    headers[c] = t
    return headers


# --------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default="debug.html")
    ap.add_argument("--full", action="store_true", help="HTMLを省略せず全文表示")
    args = ap.parse_args()

    if not os.path.exists(args.file):
        raise SystemExit(f"{args.file} がありません。先に fetch_debug.py を実行してください。")

    with open(args.file, encoding="utf-8") as f:
        html = f.read()
    soup = BeautifulSoup(html, "lxml")

    # ---------------- A. ページの素性 ----------------
    print(SEP)
    print("A. ページの素性")
    print(SEP)
    tables = soup.find_all("table")
    print(f"HTML長          : {len(html)} 文字")
    print(f"<table>          : {len(tables)}")
    print(f"<img>            : {len(soup.find_all('img'))}")
    print(f"<script>         : {len(soup.find_all('script'))}")
    print(f"<title>          : {norm(soup.title.get_text()) if soup.title else '(なし)'}")

    print()
    print("生HTMLに『F1』〜『F4』の文字列が存在するか（JSレンダリング判定の第一手がかり）:")
    any_family = False
    for i in range(1, 5):
        n = html.count(f"F{i}")
        any_family = any_family or n > 0
        print(f"  'F{i}' : {n} 箇所")

    print()
    print("JSフレームワーク／非同期取得の痕跡:")
    hits = [m for m in JS_MARKERS if m in html]
    print(f"  {hits if hits else '(検出なし)'}")

    noscripts = soup.find_all("noscript")
    if noscripts:
        print()
        print("<noscript> の内容（JS必須サイトかどうかの手がかり）:")
        for ns in noscripts[:3]:
            print(f"  {clip(ns.get_text(), 300, args.full)}")

    json_scripts = [s for s in soup.find_all("script") if (s.get("type") or "").endswith("json")]
    if json_scripts:
        print()
        print(f"JSON埋め込み <script>: {len(json_scripts)} 件（空き状況がここに入っている可能性）")
        for s in json_scripts[:3]:
            print(f"  type={s.get('type')} id={s.get('id')} : {clip(s.string or '', 300, args.full)}")

    # ---------------- B. F1〜F4 の行 ----------------
    print()
    print(SEP)
    print("B. 『F1』〜『F4』を含む行と、その各セルの生HTML")
    print(SEP)

    matched_any = False
    for ti, table in enumerate(tables):
        rows, grid = build_grid(table)
        heads = header_texts(rows, grid)
        rows_hit = []
        for r, tr in enumerate(rows):
            if FAMILY_RE.search(norm(tr.get_text())):
                rows_hit.append((r, tr))
        if not rows_hit:
            continue
        matched_any = True

        print()
        print(f"■ table[{ti}]  行数={len(rows)}  該当行={len(rows_hit)}")
        if heads:
            print(f"  推定ヘッダ列: " + ", ".join(
                f"[{c}]={t!r}" for c, t in sorted(heads.items())[:12]))
        print(SUB)

        for r, tr in rows_hit:
            label = norm(tr.get_text())
            print(f"\n  ● 行 r={r} : {clip(label, 120, args.full)}")
            cells = sorted(((c, cell) for (rr, c), cell in grid.items() if rr == r))
            for c, cell in cells[:10]:
                head = heads.get(c, "")
                print(f"    - 列[{c}] ヘッダ={head!r}")
                print(f"      生HTML: {clip(str(cell), 500, args.full)}")

    if not matched_any:
        print("  → 生HTML中の <tr> に『F1』〜『F4』を含む行は 1 件も存在しません。")
        if not any_family:
            print("     文字列自体もHTMLに無いため、表はJavaScriptで後から描画されている可能性が高いです。")

    # ---------------- C. セル表現のインベントリ ----------------
    print()
    print(SEP)
    print("C. 表セルの表現インベントリ（空き/満の差分はここに現れる）")
    print(SEP)
    counter = collections.Counter()
    examples = {}
    for table in tables:
        for cell in table.find_all(["td", "th"]):
            sig = cell_signature(cell)
            k = sig_key(sig)
            counter[k] += 1
            examples.setdefault(k, (sig, cell))

    if not counter:
        print("  表セルが 1 つも存在しません（＝表は生HTMLに無い）。")
    else:
        print(f"  異なるセル表現: {len(counter)} 種類 / 総セル数 {sum(counter.values())}")
        print(SUB)
        for k, n in counter.most_common(30):
            sig, cell = examples[k]
            print(f"\n  [{n:4d}回] {describe_sig(sig)}")
            print(f"          例: {clip(str(cell), 300, args.full)}")

    # ---------------- D. 画像とalt の全体集計 ----------------
    print()
    print(SEP)
    print("D. ページ全体の img src / alt 集計（○×アイコンの候補）")
    print(SEP)
    src_c = collections.Counter()
    alt_c = collections.Counter()
    for img in soup.find_all("img"):
        src_c[basename(img.get("src"))] += 1
        alt_c[norm(img.get("alt"))] += 1
    if src_c:
        print("\n  src(ファイル名)別:")
        for s, n in src_c.most_common(30):
            print(f"    {n:4d}回  {s}")
        print("\n  alt別:")
        for a, n in alt_c.most_common(30):
            print(f"    {n:4d}回  {a!r}")
    else:
        print("  <img> が存在しません。")

    # 記号テキストの集計（画像ではなく文字で○×を出している場合）
    print()
    marks = collections.Counter()
    for ch in html:
        if ch in "○◯●△×✕✖－-満空":
            marks[ch] += 1
    print("  記号文字の出現数（画像ではなく文字で表現している場合の手がかり）:")
    print(f"    {dict(marks) if marks else '(なし)'}")

    # ---------------- E. 結論の材料 ----------------
    print()
    print(SEP)
    print("E. まとめ（この出力を読んで人間が確定させること）")
    print(SEP)
    print("  1. B に F1〜F4 の行が出ていれば、生HTMLだけで行の特定が可能。")
    print("  2. C で 2 種類以上のセル表現が出ていれば、その差分が空き/満の区別。")
    print("     差分が img src なのか alt なのか class なのかを、この出力から確定させる。")
    print("  3. B も C も空、かつ A で 'F1' の文字列が 0 箇所なら、")
    print("     表はJavaScript描画。requestsでは取得不能 → Playwright が必要。")
    print()


if __name__ == "__main__":
    sys.exit(main())
