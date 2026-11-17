#!/usr/bin/env python3
"""Build a separate seeded Unity WebGL evaluator; never modify the purchased source."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def prepare(source, project):
    source, project = Path(source).resolve(), Path(project).resolve()
    if project.exists():
        raise ValueError("project output already exists; choose an empty directory")
    if source == project or source in project.parents or project in source.parents:
        raise ValueError("source and output must be disjoint")
    required = ["Assets/SORENGAMEFIXED/Script/MainManager.cs", "Assets/Plugins/WebGL/SorenBridge.jslib",
                "ProjectSettings/ProjectVersion.txt", "Packages/manifest.json"]
    for name in required:
        if not (source / name).is_file():
            raise ValueError(f"source missing: {name}")
    if "m_EditorVersion: 2022.3.62f3" not in (source / "ProjectSettings/ProjectVersion.txt").read_text():
        raise ValueError("requires the prepared Unity 2022.3.62f3 project")
    # Copy project inputs only: no repository, Library, caches or build artifacts.
    for name in ("Assets", "Packages", "ProjectSettings"):
        shutil.copytree(source / name, project / name,
                        ignore=shutil.ignore_patterns("._*", "*.bak*", "*.cs.bak*", ".DS_Store"))
    additions = ROOT / "tools/simulation"
    (project / "Assets/Editor").mkdir(exist_ok=True)
    shutil.copy2(additions / "SorenSimulationBuilder.cs", project / "Assets/Editor")
    shutil.copy2(additions / "SorenSimulationBridge.cs", project / "Assets")
    shutil.copy2(additions / "SorenSimulation.jslib", project / "Assets/Plugins/WebGL")
    # Isolate the piece queue from visual effects consuming Unity's global RNG.
    manager = project / required[0]
    text = manager.read_text()
    needle = "return Random.Range(0, republicControllerPrefabs.Length);"
    if text.count(needle) != 1:
        raise ValueError("unsupported GetRandomNext implementation; refusing an unpaired build")
    manager.write_text(text.replace(needle, "return SorenSimulationBridge.NextIndex(republicControllerPrefabs.Length);"))
    # Bind the exact purchased scripts, colliders, scene and physics configuration.
    hashes = {str(p.relative_to(project)): hashlib.sha256(p.read_bytes()).hexdigest()
              for name in ("Assets", "Packages", "ProjectSettings")
              for p in sorted((project / name).rglob("*")) if p.is_file()}
    return {"schema": 1, "unity_version": "2022.3.62f3", "inputs_sha256": hashes}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--source", required=True)
    ap.add_argument("--editor", required=True, help="Unity 2022.3.62f3 .app bundle on macOS")
    ap.add_argument("--out", default="tmp/local-simulation")
    ap.add_argument("--unity-cli", default=str(Path.home() / ".unity/bin/unity"))
    ap.add_argument("--timeout", type=int, default=1800)
    args = ap.parse_args()
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=False)
    manifest = prepare(args.source, out / "project")
    (out / "inputs.json").write_text(json.dumps(manifest, indent=2) + "\n")
    env = {k: v for k, v in os.environ.items() if k in ("PATH", "HOME", "TMPDIR", "LANG")}
    env["SOREN_SIMULATION_BUILD"] = str(out / "build")
    cmd = [args.unity_cli, "run", str(out / "project"), "--editor-path", args.editor,
           "--timeout", str(args.timeout), "--log-file", str(out / "build.log"), "--no-tail",
           "--", "-nographics", "-buildTarget", "WebGL", "-executeMethod", "SorenSimulationBuilder.Build"]
    result = subprocess.run(cmd, env=env)
    if result.returncode:
        raise SystemExit(result.returncode)
    manifest["build_sha256"] = {str(p.relative_to(out / "build")): hashlib.sha256(p.read_bytes()).hexdigest()
                                for p in sorted((out / "build").rglob("*")) if p.is_file()}
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(out / "build")


if __name__ == "__main__":
    main()
