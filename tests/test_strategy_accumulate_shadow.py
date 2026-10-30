"""soviet_now#145: 抽出された accumulate module の shadow 等価性テスト。

issue 受入条件「保存 game corpus で旧新の score/採否判断が許容差内」を、
旧実装の遷移を模した参照実装との一致として pin する。
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import strategy_accumulate as A


def _legacy_accumulate(acc, archive_file, score, soviet, strategy_hash,
                       russia, raw_score=''):
    """improve.sh inline python の逐語的な参照写し（テスト内の旧実装）。"""
    if acc is None:
        acc = {'files': [], 'scores': '', 'soviet': False, 'soviet_count': 0,
               'count': 0, 'hash': '', 'russia_count': 0}
    curr_hash = strategy_hash
    if acc.get('hash') and curr_hash and acc.get('hash') != curr_hash:
        acc = {'files': [], 'scores': '', 'soviet': False, 'soviet_count': 0,
               'count': 0, 'hash': curr_hash, 'russia_count': 0}
    elif curr_hash:
        acc['hash'] = curr_hash
    acc['files'].append(archive_file)
    acc['scores'] = (acc['scores'] + ' ' + str(score)).strip()
    if raw_score:
        acc['raw_scores'] = (acc.get('raw_scores', '') + ' ' + raw_score).strip()
    if soviet == 'true':
        acc['soviet'] = True
        acc['soviet_count'] = acc.get('soviet_count', 0) + 1
    if russia == 'true':
        acc['russia_count'] = acc.get('russia_count', 0) + 1
    acc['count'] += 1
    max_type, frontier_hint, peak_counts, dg = A.summarize_archive(archive_file)
    acc.setdefault('max_types', []).append(max_type)
    acc.setdefault('frontier_hints', []).append(frontier_hint)
    acc.setdefault('peak_high_type_counts', []).append(peak_counts)
    acc.setdefault('deadline_guard_counts', []).append(dg)
    acc['best_max_type'] = max([int(acc.get('best_max_type', 0) or 0), max_type])
    acc['deadline_guard_total'] = int(acc.get('deadline_guard_total', 0) or 0) + dg
    return acc


def _write_archive(tmp, name, rows):
    path = os.path.join(tmp, name)
    with open(path, 'w') as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")
    return path


class AccumulateShadowTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def _corpus(self):
        """型・DEADLINE_GUARD・壊れ行を含む小 corpus（3試合分）。"""
        return [
            (_write_archive(self.tmp, "g1.jsonl", [
                {"decision_reason": "normal",
                 "state_snapshot": {"pieces": [{"type": 5}, {"type": 11}, {"type": 11}]}},
                {"decision_reason": "DEADLINE_GUARD fallback",
                 "state_snapshot": {"pieces": [{"type": 12}]}},
            ]), 1200, 'false', 'hashA', 'false'),
            (_write_archive(self.tmp, "g2.jsonl", [
                {"decision_reason": "normal",
                 "state_snapshot": {"pieces": [{"type": 14}, {"type": 14}, {"type": 14}]}},
                {"decision_reason": "DEADLINE_GUARD",
                 "state_snapshot": {"pieces": []}},
                {"not-json": True},
                "broken line",
            ]), 800, 'true', 'hashA', 'false'),
            ("", 950, 'false', 'hashB', 'true'),  # archive なし + hash 切替
        ]

    def test_module_matches_legacy_on_corpus(self):
        legacy, module = None, None
        for archive, score, soviet, h, russia in self._corpus():
            legacy = _legacy_accumulate(legacy, archive, score, soviet, h, russia)
            module = A.accumulate(module, archive, score, soviet, h, russia)
            self.assertEqual(module, legacy, f"drift at {archive}")
        # 3試合目で hashA→hashB に切り替わり acc がリセットされる
        self.assertEqual(module['count'], 1)
        self.assertEqual(module['soviet_count'], 0)
        self.assertEqual(module['russia_count'], 1)
        self.assertEqual(module['best_max_type'], 0)
        self.assertEqual(module['hash'], 'hashB')

    def test_shadow_run_compares_to_legacy_file(self):
        shadow_acc = os.path.join(self.tmp, "shadow.json")
        legacy_acc = os.path.join(self.tmp, "legacy.json")
        archive, score, soviet, h, russia = self._corpus()[0]

        # 旧実装の書き込みを模した legacy acc
        legacy = _legacy_accumulate(None, archive, score, soviet, h, russia)
        with open(legacy_acc, 'w') as f:
            json.dump(legacy, f)

        out = A.shadow_run(shadow_acc, archive, score, soviet, h, russia,
                           legacy_acc_file=legacy_acc)
        self.assertEqual(out['count'], 1)
        self.assertTrue(out['compare']['match'])
        # shadow acc が旧ファイルとは別に永続化されている
        with open(shadow_acc) as f:
            self.assertEqual(json.load(f)['count'], 1)
        self.assertFalse(os.path.exists(shadow_acc + '.tmp'))

    def test_shadow_run_flags_divergence(self):
        shadow_acc = os.path.join(self.tmp, "shadow.json")
        archive, score, soviet, h, russia = self._corpus()[0]
        # legacy が別 hash（旧側だけ別状態）なら差分が記録される
        legacy_acc = os.path.join(self.tmp, "legacy.json")
        legacy = _legacy_accumulate(None, archive, score, soviet, 'other', russia)
        with open(legacy_acc, 'w') as f:
            json.dump(legacy, f)
        out = A.shadow_run(shadow_acc, archive, score, soviet, h, russia,
                           legacy_acc_file=legacy_acc)
        self.assertFalse(out['compare']['match'])

    def test_summarize_archive_tolerates_missing_and_broken(self):
        self.assertEqual(A.summarize_archive(''),
                         (0, 'no-high-type', 'none', 0))
        path = _write_archive(self.tmp, "broken.jsonl", ["garbage", 123])
        self.assertEqual(A.summarize_archive(path),
                         (0, 'no-high-type', 'none', 0))


if __name__ == "__main__":
    unittest.main()
