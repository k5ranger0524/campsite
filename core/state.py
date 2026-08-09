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

    def update(self, target_id, items, notified_keys, now, date_str=None,
               eligible=None):
        """1つの対象の状態を書き込む。正常にパースできた時だけ呼ぶこと。

        notified_at は「空きあり中の最終通知時刻」。
        空きあり以外に戻った対象は None にして通知履歴をリセットする。

        中身が前回と同じなら書き込まない（更新時刻だけが変わるのを避ける）。
        短い間隔で回すとき、変化が無いのに毎回コミットが増えるのを防ぐため。
        戻り値は実際に書き込んだかどうか。
        """
        previous = self.items_for(target_id)
        if eligible is None:
            eligible = {k for k, v in items.items() if st.is_available(v["status"])}

        out = {}
        for key, info in items.items():
            # 通知対象から外れた項目は履歴を消す。
            # 例: 3日以上で通知する設定で2日に減った場合、次に3日に戻ったときは
            #     残っていた日も含めて「新規」としてまとめて通知される。
            if key not in eligible:
                notified_at = None
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


def series_of(key, info):
    """通知をまとめる単位。アダプタが series を付けていなければ項目そのもの。"""
    return (info or {}).get("series") or key


def eligible_keys(current, min_available=1, priority_dates=()):
    """通知の対象にしてよい項目を返す。

    min_available が2以上なら、「同じ系列（駐車場×種別など）の中で、
    空きが min_available 日以上あるとき」だけ通知の対象になる。
    1日だけ空いても泊まれない、という条件をここで表現する。

    priority_dates に入れた日は例外で、1日だけ空いても通知の対象になる。
    「まとまって取れたら嬉しいが、この日だけは単独でも知りたい」を表す。
    """
    available = {k for k, v in current.items() if st.is_available(v["status"])}
    if min_available <= 1:
        return available

    prio = set(priority_dates or ())
    ok = {k for k in available if (current[k].get("date") in prio)}

    by_series = {}
    for key in available:
        by_series.setdefault(series_of(key, current[key]), []).append(key)

    for series, avail in by_series.items():
        if len(avail) >= min_available:
            ok.update(avail)
            continue
        skipped = sorted(k for k in avail if k not in ok)
        if skipped:
            label = current[avail[0]].get("series_name") or series
            log(f"  {label}: 空きは {len(avail)}日 "
                f"（{min_available}日以上で通知）→ 見送り: {', '.join(skipped)}")
    return ok


def decide_notifications(current, previous, now, repeat_hours, min_available=1,
                         priority_dates=()):
    """通知すべきキーを (新規, 継続中) に分けて返す。

    新規  : 通知対象になっていなかったものが、通知対象になった
    継続中: 通知対象のままで、最終通知から repeat_hours 以上経過した

    min_available が2以上のときは、条件を満たさない系列の項目は
    空きがあっても通知対象にしない（notified_at も持たせない）。
    ただし priority_dates の日は単独でも通知対象になる。
    そのため、条件を満たした時点で「空いている日がまとめて」通知される。
    """
    new_keys, cont_keys = [], []
    interval = timedelta(hours=repeat_hours)
    ok = eligible_keys(current, min_available, priority_dates)

    for key in current:
        if key not in ok:
            continue
        prev = previous.get(key) or {}
        last = parse_dt(prev.get("notified_at"))

        if last is None:
            new_keys.append(key)
        elif now - last >= interval:
            cont_keys.append(key)
        else:
            mins = int((interval - (now - last)).total_seconds() // 60)
            log(f"  {key}: 空きあり継続中だが前回通知から {mins} 分後まで再通知しません")

    return new_keys, cont_keys, ok
