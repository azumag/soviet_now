"""Append-only strategy run journal (soviet_now#145).

``accumulate -> evaluate`` の各段階を、version 付きの run journal へ
shadow 記録するための最小 API。旧実装（acc JSON / rolling scores）は
変更せず、この journal が並行して残す判断証跡を後続の apply/rollback
再設計へ入力する。記録だけなので本モジュール自体が active strategy や
production 動作へ影響することはない。

journal は 1 ファイル = 1 行 = 1 イベントの JSONL。append は tmp ファイルに
既存内容+新行を書いて fsync してから rename（または O_APPEND write）する
ことで、中断しても部分行が残らないよう atomic にする。

run_id 二重開始の拒否は、begin 時に journal を走査して同一 run_id の
open run が既に存在しないことを確認する（crash-safe ではなく advisory:
 同時並行書き込みは fcntl.flock で直列化する）。
"""

from __future__ import annotations

import fcntl
import json
import os
import time
from typing import Any, Dict, Optional

SCHEMA_VERSION = 1
DEFAULT_JOURNAL = "tmp/state/strategy_run_journal.jsonl"

_REQUIRED_FIELDS = (
    "run_id", "from_state", "to_state", "input_hash", "output_hash",
    "reason", "evidence", "owner", "time",
)


def _new_entry(run_id, from_state, to_state, reason, owner,
               input_hash="", output_hash="", evidence=None, now=None):
    """schema 全フィールドを揃えた entry を返す。"""
    return {
        "schema": SCHEMA_VERSION,
        "run_id": str(run_id),
        "from_state": str(from_state or ""),
        "to_state": str(to_state or ""),
        "input_hash": str(input_hash or ""),
        "output_hash": str(output_hash or ""),
        "reason": str(reason or ""),
        "evidence": evidence if evidence is not None else {},
        "owner": str(owner or "strategy"),
        "time": float(now if now is not None else time.time()),
    }


class StrategyRunJournal:
    """run journal への append だけを提供する shadow 記録器。"""

    def __init__(self, path=DEFAULT_JOURNAL):
        self.path = path

    def _locked(self, fn):
        """journal への全書き込みを flock 付きで直列化する。"""
        parent = os.path.dirname(self.path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        lock_path = self.path + ".lock"
        with open(lock_path, "a+") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            try:
                return fn()
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

    def _open_run_ids(self):
        """finish されていない run_id を返す（lock 内で呼ぶこと）。"""
        open_ids = set()
        if not os.path.exists(self.path):
            return open_ids
        with open(self.path, encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except Exception:
                    continue  # 末尾の部分書き込みだけを無視する
                if not isinstance(entry, dict):
                    continue
                rid = entry.get("run_id")
                if not rid:
                    continue
                if entry.get("to_state") == "finished":
                    open_ids.discard(rid)
                elif entry.get("to_state") == "started":
                    open_ids.add(rid)
        return open_ids

    def begin(self, run_id, reason="", owner="strategy", evidence=None,
              input_hash="", now=None):
        """同一 run_id が既に open なら拒否し、started を記録する。"""
        def work():
            if run_id in self._open_run_ids():
                raise ValueError(f"run_id already open: {run_id}")
            self._append(_new_entry(
                run_id, "new", "started", reason, owner,
                input_hash=input_hash, evidence=evidence, now=now))
            return True
        return self._locked(work)

    def append(self, run_id, from_state, to_state, reason="", owner="strategy",
               input_hash="", output_hash="", evidence=None, now=None):
        """run の途中イベントを記録する（run_id の open 検査はしない）。"""
        def work():
            self._append(_new_entry(
                run_id, from_state, to_state, reason, owner,
                input_hash=input_hash, output_hash=output_hash,
                evidence=evidence, now=now))
            return True
        return self._locked(work)

    def finish(self, run_id, outcome, reason="", owner="strategy",
               output_hash="", evidence=None, now=None):
        """run を finished へ遷移させる。outcome は applied/rejected/failed 等。"""
        def work():
            self._append(_new_entry(
                run_id, outcome or "unknown", "finished", reason, owner,
                output_hash=output_hash, evidence=evidence, now=now))
            return True
        return self._locked(work)

    def _append(self, entry):
        """既存行を保持したまま 1 行を追記する。tmp+rename で部分行を残さない。"""
        line = json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n"
        tmp = self.path + ".tmp"
        # journal はイベント数が少ない想定なので、tmp へ全行コピー+追記して
        # rename する方式を選ぶ（O_APPEND だと部分 write が残りうるため）。
        parent = os.path.dirname(self.path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as out:
            if os.path.exists(self.path):
                with open(self.path, encoding="utf-8", errors="ignore") as src:
                    for src_line in src:
                        out.write(src_line if src_line.endswith("\n")
                                  else src_line + "\n")
            out.write(line)
            out.flush()
            os.fsync(out.fileno())
        os.replace(tmp, self.path)


def shadow_compare(old: Dict[str, Any], new: Dict[str, Any],
                   keys=None, tolerance=0.0) -> Dict[str, Any]:
    """旧実装の結果 dict と新 module の結果 dict を shadow 比較する。

    ``keys`` が指定されればそのフィールドだけを比べる。数値は
    ``tolerance`` 以内を一致とみなす。差分は ``{"field": [old, new]}``
    で返し、呼出側が journal の evidence へ載せる。
    """
    keys = keys if keys is not None else sorted(set(old) | set(new))
    diff = {}
    for key in keys:
        old_v, new_v = old.get(key), new.get(key)
        if isinstance(old_v, (int, float)) and isinstance(new_v, (int, float)):
            if abs(float(old_v) - float(new_v)) > tolerance:
                diff[key] = [old_v, new_v]
        elif old_v != new_v:
            diff[key] = [old_v, new_v]
    return {"match": not diff, "diff": diff}


def _cli(argv=None):
    """shell 呼出し用の薄い CLI。journal 書き込み失敗は常に rc=1 で報告し、
    呼出側（|| true 付きで呼ぶ shadow 経路）が production を止めない。"""
    import argparse
    import sys

    parser = argparse.ArgumentParser(prog="strategy_run_journal")
    parser.add_argument("--journal", default=DEFAULT_JOURNAL)
    parser.add_argument("op", choices=("begin", "append", "finish"))
    parser.add_argument("run_id")
    parser.add_argument("--from-state", dest="from_state", default="")
    parser.add_argument("--to-state", dest="to_state", default="")
    parser.add_argument("--reason", default="")
    parser.add_argument("--owner", default="strategy")
    parser.add_argument("--input-hash", dest="input_hash", default="")
    parser.add_argument("--output-hash", dest="output_hash", default="")
    parser.add_argument("--evidence-json", dest="evidence_json", default="")
    args = parser.parse_args(argv)

    evidence = None
    if args.evidence_json:
        try:
            evidence = json.loads(args.evidence_json)
        except Exception:
            evidence = {"raw": args.evidence_json}

    journal = StrategyRunJournal(args.journal)
    try:
        if args.op == "begin":
            journal.begin(args.run_id, reason=args.reason, owner=args.owner,
                          input_hash=args.input_hash, evidence=evidence)
        elif args.op == "finish":
            journal.finish(args.run_id, args.to_state or "unknown",
                           reason=args.reason, owner=args.owner,
                           output_hash=args.output_hash, evidence=evidence)
        else:
            journal.append(args.run_id, args.from_state, args.to_state,
                           reason=args.reason, owner=args.owner,
                           input_hash=args.input_hash,
                           output_hash=args.output_hash, evidence=evidence)
    except ValueError as e:
        print(f"strategy_run_journal: {e}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"strategy_run_journal: write failed: {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
