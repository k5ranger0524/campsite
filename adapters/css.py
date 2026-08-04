"""CSSセレクタで読む汎用アダプタ。専用アダプタを書かずに済ませるためのもの。

「ページ内の決まった場所に『空車』『満室』などの文字かclassが出る」型のサイトなら、
これで targets.yml の設定だけで追加できる。駐車場の満空表示や、宿の在庫表示など。

日付グリッドを持つサイト（489ban など）には向かない。専用アダプタを書くこと。

config:
    url: https://example.com/parking          # {date} を含めると日付が埋め込まれる
    items:
        P1:
            selector: "#lot-1 .status"        # 状態が書かれている要素
            name: 第1駐車場                    # 通知に出す名前（省略可）
    rules:                                     # 上から順に最初に一致したものを採用
        - contains: 空車                       # 要素のテキストに含まれる
          status: available
        - class: is-full                       # 要素（または子孫）が持つclass
          status: full
        - regex: 残り\\s*[1-9]                  # 正規表現
          status: available
    default: full                              # どのruleにも一致しない場合（省略時はエラー）

discover 用（任意）:
    discover_selector: ".parking-lot"          # 候補を洗い出すセレクタ
"""

import re

from bs4 import BeautifulSoup

from core import status as st
from core.util import MonitorError

VALID = set(st.ALL)


def build_url(config, date_str):
    url = config.get("url")
    if not url:
        raise MonitorError("config に url がありません")
    if "{date}" in url:
        if not date_str:
            raise MonitorError("url に {date} があるので date が必要です")
        url = url.replace("{date}", str(date_str))
    return url


def _rules(config):
    rules = config.get("rules")
    if not isinstance(rules, list) or not rules:
        raise MonitorError("config に rules がありません")
    for i, rule in enumerate(rules):
        if not isinstance(rule, dict):
            raise MonitorError(f"rules[{i}] が辞書ではありません")
        status = rule.get("status")
        if status not in VALID:
            raise MonitorError(
                f"rules[{i}] の status が不正です: {status!r}（{'/'.join(st.ALL)}）"
            )
        if not any(k in rule for k in ("contains", "class", "regex")):
            raise MonitorError(
                f"rules[{i}] に contains / class / regex のいずれかが必要です"
            )
    return rules


def _classes(el):
    found = set(el.get("class") or [])
    for child in el.find_all(True):
        found.update(child.get("class") or [])
    return found


def _match(rule, text, classes):
    if "contains" in rule and str(rule["contains"]) in text:
        return True
    if "class" in rule and str(rule["class"]) in classes:
        return True
    if "regex" in rule:
        try:
            if re.search(str(rule["regex"]), text):
                return True
        except re.error as exc:
            raise MonitorError(f"rules の regex が不正です: {exc}")
    return False


def _classify(el, rules, config, where):
    text = re.sub(r"\s+", " ", el.get_text(" ", strip=True))
    classes = _classes(el)

    for rule in rules:
        if _match(rule, text, classes):
            return rule["status"], text

    default = config.get("default")
    if default:
        if default not in VALID:
            raise MonitorError(f"default が不正です: {default!r}")
        return default, text

    # 黙って unknown にはしない。判定できない＝サイトが変わった可能性なので失敗させる。
    raise MonitorError(
        f"{where}: どの rule にも一致しませんでした（テキスト={text[:80]!r} "
        f"class={sorted(classes)[:8]}）"
    )


def parse(html, config, date_str, warn=None):
    warn = warn or (lambda msg: None)
    rules = _rules(config)
    items_cfg = config.get("items") or {}
    if not items_cfg:
        raise MonitorError("config に items がありません")

    soup = BeautifulSoup(html, "lxml")

    result, missing = {}, []
    for key, spec in items_cfg.items():
        if isinstance(spec, str):
            spec = {"selector": spec}
        selector = spec.get("selector")
        if not selector:
            raise MonitorError(f"{key}: selector がありません")

        try:
            el = soup.select_one(selector)
        except Exception as exc:                     # セレクタ自体が不正
            raise MonitorError(f"{key}: selector が不正です {selector!r}: {exc}")

        if el is None:
            missing.append(f"{key}({selector})")
            continue

        status, text = _classify(el, rules, config, key)
        result[key] = {
            "name": spec.get("name") or key,
            "status": status,
            "text": text,
        }

    if missing:
        raise MonitorError(
            "監視対象が見つかりません: " + ", ".join(missing)
            + "。サイト側のHTML構造が変わった可能性があります"
        )

    return result


def discover(html, config, date_str, warn=None):
    """discover_selector に一致する要素を洗い出す。"""
    selector = config.get("discover_selector")
    if not selector:
        raise MonitorError(
            "discover には config に discover_selector が必要です "
            "（例: --config discover_selector=.parking-lot）"
        )
    soup = BeautifulSoup(html, "lxml")
    rules = config.get("rules") or []

    found = []
    for i, el in enumerate(soup.select(selector), 1):
        text = re.sub(r"\s+", " ", el.get_text(" ", strip=True))
        status = st.UNKNOWN
        if rules:
            for rule in rules:
                if _match(rule, text, _classes(el)):
                    status = rule["status"]
                    break
        ident = el.get("id") or f"{selector}:nth-of-type({i})"
        found.append({"key": ident, "name": text[:60], "status": status})
    return found
