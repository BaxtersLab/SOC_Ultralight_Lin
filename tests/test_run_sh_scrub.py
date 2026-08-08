"""The snap-contamination scrub in run.sh, executed rather than pattern-matched.

Snap contamination is the #1 time sink on this box, and the scrub is shell that
no other test covers — a typo in it would only ever surface as SOC dying at
launch with `symbol lookup error: undefined symbol: __libc_pthread_init`, which
reads like a Python bug and is not one.

So these tests do not grep run.sh for the variable names. They cut the SHIPPED
block out of run.sh between its markers, run it under bash with an environment
copied from a real contaminated VS Code terminal on this box (18 leaked
variables, captured 2026-08-07), and assert on the environment that survives.
An edit that breaks the block therefore fails here, not at launch.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

RUN_SH = Path(__file__).resolve().parent.parent / "run.sh"
BEGIN = "# (scrub:begin)"
END = "# (scrub:end)"

# Copied from a live VS Code terminal on this box. Two shapes matter and the
# block catches them by different rules: values starting with /snap/ (caught by
# the env-scanning loop) and values under $HOME/snap/ (caught by the explicit
# list — they start with /home/, so the first loop never sees them).
CONTAMINATED = {
    "LOCPATH": "/snap/code/254/usr/lib/locale",
    "GTK_PATH": "/snap/code/254/usr/lib/x86_64-linux-gnu/gtk-3.0",
    "GTK_IM_MODULE_FILE": "/home/baxter/snap/code/254/.cache/immodules.cache",
    "GTK_EXE_PREFIX": "/snap/code/254/usr",
    "GIO_MODULE_DIR": "/home/baxter/snap/code/254/.cache/gio-modules",
    "GSETTINGS_SCHEMA_DIR": "/home/baxter/snap/code/254/.local/share/glib-2.0/schemas",
    "GDK_PIXBUF_MODULEDIR": "/snap/code/254/usr/lib/x86_64-linux-gnu/gdk-pixbuf-2.0/2.10.0/loaders",
    "GDK_PIXBUF_MODULE_FILE": "/home/baxter/snap/code/254/.cache/gdk-pixbuf-loaders.cache",
    "XDG_DATA_HOME": "/home/baxter/snap/code/254/.local/share",
    "SNAP": "/snap/code/254",
    # Survive by design — see INERT below. Present so the test environment
    # matches a real contaminated shell rather than a flattering subset.
    "SNAP_DATA": "/var/snap/code/254",
    "SNAP_COMMON": "/var/snap/code/common",
    "SNAP_USER_DATA": "/home/baxter/snap/code/254",
    "SNAP_USER_COMMON": "/home/baxter/snap/code/common",
    "PATH": "/usr/local/bin:/usr/bin:/bin:/snap/bin",
    "VSCODE_NLS_CONFIG": '{"defaultMessagesFile":"/snap/code/254/usr/share/code/nls.json"}',
    "XDG_DATA_DIRS": (
        "/home/baxter/snap/code/254/.local/share:/home/baxter/snap/code/254:"
        "/snap/code/254/usr/share:/usr/share/ubuntu:/usr/share/gnome:"
        "/usr/local/share/:/usr/share/:/var/lib/snapd/desktop"
    ),
    # Not contamination. Present to prove the block is a scrub, not a reset.
    "HOME": "/home/baxter",
    "SOC_CANARY": "untouched",
}

# The loader variables. Any one of these left pointing into a snap is a
# different-glibc / wrong-schema failure at launch; LOCPATH is the fatal one.
FATAL = [
    "LOCPATH", "GTK_PATH", "GTK_IM_MODULE_FILE", "GTK_EXE_PREFIX",
    "GIO_MODULE_DIR", "GSETTINGS_SCHEMA_DIR", "GDK_PIXBUF_MODULEDIR",
    "GDK_PIXBUF_MODULE_FILE", "XDG_DATA_HOME",
]


def scrub_block() -> str:
    """The scrub exactly as run.sh ships it."""
    text = RUN_SH.read_text(encoding="utf-8")
    if BEGIN not in text or END not in text:
        pytest.fail(
            f"run.sh has no {BEGIN} / {END} markers — either the scrub is "
            f"missing or the markers were renamed without updating this test."
        )
    return text.split(BEGIN, 1)[1].split(END, 1)[0]


@pytest.fixture(scope="module")
def scrubbed() -> dict[str, str]:
    """Environment surviving the shipped block, run under a contaminated env."""
    proc = subprocess.run(
        ["bash", "-c", scrub_block() + "\nenv -0"],
        env=CONTAMINATED, capture_output=True, text=True, timeout=30,
    )
    assert proc.returncode == 0, f"scrub block errored:\n{proc.stderr}"
    out = {}
    for entry in proc.stdout.split("\0"):
        if "=" in entry:
            k, v = entry.split("=", 1)
            out[k] = v
    return out


@pytest.mark.parametrize("var", FATAL)
def test_fatal_loader_var_is_unset(scrubbed, var):
    assert var not in scrubbed, (
        f"{var}={scrubbed.get(var)!r} survived the scrub — a snap-owned "
        f"loader path reaching SOC's interpreter."
    )


# Variables that legitimately still name a snap path after the scrub. None of
# them is consulted by a loader, so none can produce the wrong-glibc or
# wrong-schema failures the scrub exists to prevent:
#   SNAP*              inert path strings; nothing dereferences them here
#   PATH               /snap/bin is wanted — CLAUDE.md: PATH needs no blanket
#                      removal, and stripping it would hide snap-installed CLIs
#   VSCODE_NLS_CONFIG  a JSON blob read only by VS Code's own runtime
INERT = ("SNAP", "SNAP_DATA", "SNAP_COMMON", "SNAP_USER_DATA",
         "SNAP_USER_COMMON", "PATH", "VSCODE_NLS_CONFIG")


def test_no_loader_variable_still_points_into_a_snap(scrubbed):
    """Catches leaks the explicit list does not name — e.g. a new GTK_* or
    GIO_* variable appearing in a future snap — without pretending the block
    scrubs the inert ones, which by design it does not.

    The exact survivors here were confirmed against a live launch: SOC started
    from a contaminated VS Code terminal kept exactly these six (2026-08-07).
    """
    leaked = {k: v for k, v in scrubbed.items()
              if "/snap/" in v and k not in INERT}
    assert not leaked, (
        f"a loader variable still points into a snap after the scrub: {leaked}"
    )


def test_xdg_data_dirs_is_filtered_not_unset(scrubbed):
    """The documented rule: XDG_DATA_DIRS is FILTERED. Unsetting it would take
    the system's icon and .desktop paths down with the snap's — which is how
    SOC's own desktop entry and icon would go missing from the shell."""
    dirs = scrubbed.get("XDG_DATA_DIRS")
    assert dirs, "XDG_DATA_DIRS was unset or emptied; it must be filtered"
    assert not [p for p in dirs.split(":") if "/snap/" in p]
    for kept in ("/usr/share/", "/usr/share/gnome", "/var/lib/snapd/desktop"):
        assert kept in dirs.split(":"), f"{kept} was dropped from XDG_DATA_DIRS"


def test_scrub_leaves_clean_variables_alone(scrubbed):
    assert scrubbed.get("SOC_CANARY") == "untouched"
    assert scrubbed.get("HOME") == "/home/baxter"


def test_scrub_runs_before_any_python_is_invoked():
    """Ordering is load-bearing, not cosmetic: run.sh creates the venv with
    `python3 -m venv`, and a contaminated LOCPATH kills that interpreter
    outright. The scrub must therefore precede the first python invocation.

    Comment lines are stripped before the search — the scrub's own comment
    block names `python3 -m venv` when explaining why it goes first, and
    matching that text would report the block as coming after itself."""
    code = [ln for ln in RUN_SH.read_text(encoding="utf-8").splitlines()
            if not ln.lstrip().startswith("#")]
    first_python = next(
        (i for i, ln in enumerate(code) if "python3" in ln or '"$PY"' in ln), None)
    assert first_python is not None, "run.sh invokes no python at all"

    marked = RUN_SH.read_text(encoding="utf-8").splitlines()
    begin_line = next(i for i, ln in enumerate(marked) if BEGIN in ln)
    # Same measure for both: how many non-comment lines precede each.
    scrub_pos = sum(1 for ln in marked[:begin_line]
                    if not ln.lstrip().startswith("#"))
    assert scrub_pos <= first_python, (
        f"the scrub block starts after run.sh's first python invocation "
        f"({code[first_python].strip()!r}) — a contaminated LOCPATH would "
        f"already have killed that interpreter"
    )
