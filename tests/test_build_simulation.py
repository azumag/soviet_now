import importlib.util
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("build_simulation", ROOT / "tools/build_simulation.py")
build = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build)


class SimulationBuildTests(unittest.TestCase):
    def setUp(self):
        (ROOT / "tmp").mkdir(exist_ok=True)

    def test_separate_project_preserves_source_and_isolates_piece_rng(self):
        base = ROOT / "tmp"
        base.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="simulation-build-unit-", dir=base) as tmp:
            source = Path(tmp) / "source"
            files = {
                "Assets/SORENGAMEFIXED/Script/MainManager.cs": "return Random.Range(0, republicControllerPrefabs.Length);",
                "Assets/Plugins/WebGL/SorenBridge.jslib": "original bridge",
                "ProjectSettings/ProjectVersion.txt": "m_EditorVersion: 2022.3.62f3",
                "Packages/manifest.json": "{}",
                "Assets/._junk": "not copied",
                "Library/cache": "not copied",
            }
            for name, text in files.items():
                p = source / name
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(text)
            project = Path(tmp) / "project"
            manifest = build.prepare(source, project)
            self.assertIn("SorenSimulationBridge.NextIndex", (project / "Assets/SORENGAMEFIXED/Script/MainManager.cs").read_text())
            self.assertEqual((source / "Assets/SORENGAMEFIXED/Script/MainManager.cs").read_text(), files["Assets/SORENGAMEFIXED/Script/MainManager.cs"])
            self.assertFalse((project / "Library").exists())
            self.assertFalse((project / "Assets/._junk").exists())
            self.assertTrue(manifest["inputs_sha256"])
            with self.assertRaises(ValueError):
                build.prepare(source, project)

    def test_missing_source_does_not_create_output(self):
        with tempfile.TemporaryDirectory(prefix="simulation-build-unit-", dir=ROOT / "tmp") as tmp:
            out = Path(tmp) / "out"
            with self.assertRaises(ValueError):
                build.prepare(Path(tmp) / "missing", out)
            self.assertFalse(out.exists())


if __name__ == "__main__":
    unittest.main()
