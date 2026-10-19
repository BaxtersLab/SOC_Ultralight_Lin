"""Split-out Debian packages that the shipped code imports must be declared.

Found 2026-09-29 by the release VM window check: SOC Ultralight crashed at launch
on a clean install (`ImportError: cannot import name 'ImageTk' from 'PIL'`).
Debian splits PIL.ImageTk into python3-pil.imagetk, and tkinter -- which Python
itself counts as standard library -- into python3-tk. Neither was in Depends; the
dev box simply had both installed, so every launch here worked.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONTROL = ROOT / "packaging" / "DEBIAN" / "control"
SKIP = ("tests", "vendor", "packaging")


def declared() -> set[str]:
    text = CONTROL.read_text()
    m = re.search(r"^Depends:(.*(?:\n[ \t].*)*)", text, re.M)
    assert m, "no Depends: in the control file"
    return {re.split(r"[\s(]", alt.strip())[0] for dep in m.group(1).replace("\n", " ").split(",")
            for alt in dep.split("|") if alt.strip()}


def shipped_sources() -> str:
    files = [p for p in ROOT.rglob("*.py")
             if not any(part in SKIP for part in p.relative_to(ROOT).parts) and not p.name.startswith("make_")]
    assert files, "found no shipped Python sources"
    return "\n".join(p.read_text(errors="replace") for p in files)


def test_pil_imagetk_is_declared_when_imported():
    src = shipped_sources()
    if re.search(r"^\s*from PIL import[^\n]*\bImageTk\b|^\s*import PIL\.ImageTk", src, re.M):
        assert "python3-pil.imagetk" in declared(), "PIL.ImageTk is imported but python3-pil.imagetk is not in Depends"


def test_tkinter_is_declared_when_imported():
    src = shipped_sources()
    if re.search(r"^\s*(import tkinter|from tkinter)", src, re.M):
        assert "python3-tk" in declared(), "tkinter is imported but python3-tk is not in Depends"


def test_the_guard_sees_the_real_imports():
    """Positive control: both imports really are in the shipped code, so the two
    checks above are live, not vacuous."""
    src = shipped_sources()
    assert re.search(r"^\s*from PIL import[^\n]*\bImageTk\b", src, re.M)
    assert re.search(r"^\s*(import tkinter|from tkinter)", src, re.M)
