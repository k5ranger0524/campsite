"""監視対象一覧(targets.yml)の読み込みと検証。"""

import os

import yaml

from .util import MonitorError

DEFAULT_REPEAT_HOURS = 2.0


class Target:
    def __init__(self, raw, defaults):
        self.id = raw.get("id")
        self.name = raw.get("name") or self.id
        self.adapter = raw.get("adapter")
        self.enabled = raw.get("enabled", True)
        self.config = raw.get("config") or {}

        # 確認頻度のグループ。どの間隔で回すかはワークフロー側が --group で選ぶ。
        # 対象を増やすときにワークフローを触らなくて済むよう、頻度もここで指定する。
        self.group = str(raw.get("group") or defaults.get("group") or "normal")

        # 日付を持たない対象（例: 駐車場の今の満空）もあるので任意
        date = raw.get("date")
        self.date = str(date) if date is not None else None

        # 「同じ系列で何日以上空いたら通知するか」。1なら1日でも通知（既定）
        self.min_available = int(
            raw.get("min_available", defaults.get("min_available", 1))
        )

        self.repeat_hours = float(
            raw.get("repeat_hours", defaults.get("repeat_hours", DEFAULT_REPEAT_HOURS))
        )

    def __repr__(self):
        return f"<Target {self.id} adapter={self.adapter}>"


def load_targets(path):
    if not os.path.exists(path):
        raise MonitorError(f"監視対象ファイルがありません: {path}")
    try:
        with open(path, encoding="utf-8") as f:
            doc = yaml.safe_load(f) or {}
    except yaml.YAMLError as exc:
        raise MonitorError(f"{path} を読めません: {exc}")

    defaults = doc.get("defaults") or {}
    raw_targets = doc.get("targets")
    if not isinstance(raw_targets, list) or not raw_targets:
        raise MonitorError(f"{path} に targets が定義されていません")

    targets, seen = [], set()
    for i, raw in enumerate(raw_targets):
        if not isinstance(raw, dict):
            raise MonitorError(f"{path}: targets[{i}] が辞書ではありません")
        t = Target(raw, defaults)
        if not t.id:
            raise MonitorError(f"{path}: targets[{i}] に id がありません")
        if not t.adapter:
            raise MonitorError(f"{path}: {t.id} に adapter がありません")
        if t.id in seen:
            raise MonitorError(f"{path}: id が重複しています: {t.id}")
        if t.repeat_hours <= 0:
            raise MonitorError(f"{path}: {t.id} の repeat_hours は正の数にしてください")
        if t.min_available < 1:
            raise MonitorError(f"{path}: {t.id} の min_available は1以上にしてください")
        seen.add(t.id)
        targets.append(t)
    return targets
