"""監視状態の保存と、通知すべきかの判定。監視対象に依存しない。

state.json は対象(target)ごとに区切って持つ。
1つの対象の取得やパースが失敗しても、その対象の状態は書き換えない
（失敗を「空きなし」と誤記録して次回に誤通知するのを防ぐため）。
"""

import json
import os
from datetime import datetime, timedelta

from . import status as st
from .util import log


def parse_dt(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except (ValueError, TypeError):
        return None


class StateStore:
    """state.json を対象単位で読み書きする。"""

    def __init__(self, path):
        self.path = path
        self.data = self._load()

    def _load(self):
        if not os.path.exists(self.path):
            return {"targets": {}}
        try:
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError) as exc:
            log(f"警告: {self.path} を読めませんでした ({exc})。初回扱いにします")
            return {"targets": {}}
        if not isinstance(data.get("targets"), dict):
            data = {"targets": {}}
        return data

    def items_for(self, target_id):
        """前回の {キー: {status, name, notified_at}} を返す。無ければ空。"""
        section = self.data["targets"].get(target_id) or {}
        items = section.get("items")
        return items if isinstance(items, dict) else {}

    def update(self, target_id, items, notified_keys, now, date_str=None):
        """1つの対象の状態を書き込む。正常にパースできた時だけ呼ぶこと。

        notified_at は「空きあり中の最終通知時刻」。
        空きあり以外に戻った対象は None にして通知履歴をリセットする。

        中身が前回と同じなら書き込まない（更新時刻だけが変わるのを避ける）。
        短い間隔で回すとき、変化が無いのに毎回コミットが増えるのを防ぐため。
        戻り値は実際に書き込んだかどうか。
        """
        previous = self.items_for(target_id)
        out = {}
        for key, info in items.items():
            if not st.is_available(info["status"]):
                notified_at = None                      # 空きが消えたらリセット
            elif key in notified_keys:
                notified_at = now.isoformat()
            else:
                notified_at = (previous.get(key) or {}).get("notified_at")
            out[key] = {
                "status": info["status"],
                "name": info.get("name"),
                "notified_at": notified_at,
            }

        if previous == out and target_id in self.data["targets"]:
            return False

        self.data["targets"][target_id] = {
            "updated_at": now.isoformat(),
            "date": date_str,
            "items": out,
        }
        self.data["updated_at"] = now.isoformat()
        self._write()
        return True

    def _write(self):
        tmp = self.path + ".tmp"
        parent = os.path.dirname(os.path.abspath(self.path))
        os.makedirs(parent, exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False, indent=2)
            f.write("\n")
        os.replace(tmp, self.path)


def decide_notifications(current, previous, now, repeat_hours):
    """通知すべきキーを (新規, 継続中) に分けて返す。

    新規  : 空きあり以外 → 空きあり に変化した
    継続中: 空きありが続いていて、最終通知から repeat_hours 以上経過した
    """
    new_keys, cont_keys = [], []
    interval = timedelta(hours=repeat_hours)

    for key, info in current.items():
        if not st.is_available(info["status"]):
            continue
        prev = previous.get(key) or {}
        last = parse_dt(prev.get("notified_at"))

        if not st.is_available(prev.get("status")) or last is None:
            new_keys.append(key)
        elif now - last >= interval:
            cont_keys.append(key)
        else:
            mins = int((interval - (now - last)).total_seconds() // 60)
            log(f"  {key}: 空きあり継続中だが前回通知から {mins} 分後まで再通知しません")

    return new_keys, cont_keys
