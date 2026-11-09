"""Load docich's corner text renderer when the shared checkout is present.

ADR 0006 stage 2 moves the corner/hanjuku status text into docich.  Soren keeps
its local implementation as the fail-open path for standalone checkouts and
for a docich deployment that has not received the module yet.  The loader is
deliberately a file import (not a package import): the production checkout is
not on sys.path and the game must start even when docich is absent.
"""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path


MODULE_NAME = "soren_corner_text"
CONTRACT = ("render_docich_corner_stats",)


def _candidate(root: str | os.PathLike[str] | None = None) -> Path:
    base = Path(root) if root is not None else Path(os.getenv("DOCICH_ROOT", "/home/ubuntu/docich"))
    return base / "src" / "docich" / f"{MODULE_NAME}.py"


def load_docich_corner_text(root: str | os.PathLike[str] | None = None):
    """Return docich's module, or None for the local fallback path."""
    path = _candidate(root)
    try:
        if not path.is_file():
            return None
        spec = importlib.util.spec_from_file_location("docich_shared_soren_corner_text", path)
        if spec is None or spec.loader is None:
            return None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        if not all(callable(getattr(module, name, None)) for name in CONTRACT):
            return None
        return module
    except Exception:
        return None
