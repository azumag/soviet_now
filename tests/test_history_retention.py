import importlib.util
import json
import pathlib
import tempfile
import unittest

SPEC = importlib.util.spec_from_file_location('retention', pathlib.Path(__file__).resolve().parents[1] / 'infra/history_retention.py')

class HistoryRetentionTest(unittest.TestCase):
    def test_batch_and_retries_survive_pruning(self):
        mod = importlib.util.module_from_spec(SPEC)
        SPEC.loader.exec_module(mod)
        with tempfile.TemporaryDirectory() as d:
            root = pathlib.Path(d)
            history = root / 'game_history'
            history.mkdir()
            files = []
            for i in range(130):
                p = history / f'{i:04d}_score0001.jsonl'
                p.write_text('{}\n')
                files.append(p)
            state = root / 'tmp/state'
            state.mkdir(parents=True)
            refs = [state/'accumulated_games.json', state/'improve_retry_batch.json', root/'tmp/improve.lock']
            for i, p in enumerate(refs):
                p.write_text(json.dumps({'files': [str(files[i].relative_to(root))]}))
            removed = mod.prune(root, 48, refs)
            self.assertEqual(removed, 27)
            self.assertTrue(all(p.exists() for p in files[:3]))
            self.assertTrue(all(p.exists() for p in files[-100:]))

    def test_large_improvement_window_is_retained(self):
        mod = importlib.util.module_from_spec(SPEC)
        SPEC.loader.exec_module(mod)
        with tempfile.TemporaryDirectory() as d:
            root = pathlib.Path(d)
            history = root/'game_history'
            history.mkdir()
            for i in range(150):
                (history/f'{i:04d}_score0001.jsonl').write_text('{}')
            self.assertEqual(mod.prune(root, 140, []), 9)
            self.assertEqual(len(list(history.iterdir())), 141)

    def test_invalid_batch_prevents_deletion(self):
        mod = importlib.util.module_from_spec(SPEC)
        SPEC.loader.exec_module(mod)
        with tempfile.TemporaryDirectory() as d:
            root = pathlib.Path(d)
            ref = root/'batch.json'
            ref.write_text('{')
            self.assertEqual(mod.prune(root, 48, [ref]), 0)

if __name__ == '__main__':
    unittest.main()
