#!/usr/bin/env bash
# SOC Ultralight — Linux launcher (counterpart to run.bat).
#
# The reason this file exists rather than "just run soc_ultralight.py":
# platform_layer.get_platform() does NOT auto-select the Wayland backend, by
# design — see its docstring. Without SOC_PLATFORM set, SOC on a Wayland session
# silently falls back to the x11 backend, where mss captures the empty X root
# window (measured: mean 0.00, stdev 0.00 — pure black) and pyautogui's XTEST
# clicks land in XWayland, which cannot reach native Wayland clients. Template
# matching against a black frame never matches, so auto-click appears to run and
# never clicks anything. This script picks the backend from the live session so
# that cannot happen by omission.
set -euo pipefail

cd "$(dirname "$(readlink -f "$0")")"

# ── Snap contamination scrub — MUST run before any python is invoked ─────────
#
# Same block as Baxters_Ai_Hot_Rod_Tuner/run.sh and SOC_Master_Widget/run.sh.
# It is FIRST because a snap-owned LOCPATH kills the interpreter outright,
# before SOC's own code runs at all -- originally this mattered because a venv
# bootstrap ran here; that is gone as of 2026-09-25, but the import check and
# the app itself are still python3, so the scrub still has to come first:
#   symbol lookup error: ... undefined symbol: __libc_pthread_init
# It points into the snap's locales, built against a different glibc.
#
# Launching from the GNOME app grid gives a clean environment, so this path is
# quiet there; it earns its place when SOC is started from a VS Code terminal,
# which on this box leaks 18 of these (measured 2026-08-07).
#
# Scrub by rule, not by a hand-picked list — the two loops catch different
# shapes: values that START with /snap/ (first loop, found by scanning env) and
# values under $HOME/snap/ (second loop; they start with /home/, so the first
# loop never sees them). XDG_DATA_DIRS is FILTERED rather than unset: it
# legitimately holds the system's icon and .desktop paths, and unsetting it
# would hide SOC's own desktop entry from the shell. /var/lib/snapd/desktop is
# snapd, not /snap/, and is kept.
#
# tests/test_run_sh_scrub.py executes this block — it is not just documented.
# (scrub:begin)
for _var in $(env | grep -o '^[A-Za-z_][A-Za-z0-9_]*=/snap/[^:]*' | cut -d= -f1); do
    [[ "$_var" == "XDG_DATA_DIRS" ]] && continue
    unset "$_var"
done
for _var in GSETTINGS_SCHEMA_DIR GTK_PATH GTK_IM_MODULE_FILE GTK_EXE_PREFIX \
            GIO_MODULE_DIR LOCPATH GDK_PIXBUF_MODULEDIR GDK_PIXBUF_MODULE_FILE \
            XDG_DATA_HOME XDG_CONFIG_HOME XDG_CACHE_HOME; do
    [[ "${!_var:-}" == *"/snap/"* ]] && unset "$_var"
done
unset _var
if [[ "${XDG_DATA_DIRS:-}" == *"/snap/"* ]]; then
    _clean=""
    IFS=':' read -ra _parts <<< "$XDG_DATA_DIRS"
    for _p in "${_parts[@]}"; do
        [[ -z "$_p" || "$_p" == */snap/* ]] && continue
        _clean="${_clean:+$_clean:}$_p"
    done
    export XDG_DATA_DIRS="${_clean:-/usr/local/share:/usr/share}"
    unset _clean _parts _p
fi
# (scrub:end)

# ── Backend selection ────────────────────────────────────────────────────────
# An explicit SOC_PLATFORM always wins, so the escape hatch still works:
#   SOC_PLATFORM=x11 ./run.sh    # force XWayland/X11 window targeting
if [[ -z "${SOC_PLATFORM:-}" ]]; then
    if [[ "${XDG_SESSION_TYPE:-}" == "wayland" || -n "${WAYLAND_DISPLAY:-}" ]]; then
        export SOC_PLATFORM=wayland
    else
        export SOC_PLATFORM=x11
    fi
fi
echo "[soc] session=${XDG_SESSION_TYPE:-unknown}  backend=${SOC_PLATFORM}"

# VS Code's extension host exports these into every terminal it spawns. They
# follow child processes and break them: GDK_BACKEND=x11 forces a "native
# Wayland" launch onto XWayland, and ELECTRON_RUN_AS_NODE makes any Electron or
# Chromium child start as a bare Node runtime.
unset GDK_BACKEND ELECTRON_RUN_AS_NODE

# ── Interpreter and dependencies ─────────────────────────────────────────────
#
# This used to build a virtualenv here and `pip install -r requirements.txt`
# into it on first launch. That cannot work for an INSTALLED copy and it should
# not have worked for a packaged one either:
#
#   * /opt/baxters/soc-ultralight is root-owned, so a normal user's first launch
#     could not create .venv at all;
#   * it reached the NETWORK at start-up, which A1 s7.1 forbids outright -- an
#     app must not need a network to open;
#   * it made the gate meaningless. The tests ran against a venv assembled by
#     pip while the product would have run against something else entirely.
#
# Everything now comes from apt, declared in the package's Depends, except two
# modules with no Ubuntu package at all (mss, pytesseract). Those are PURE
# PYTHON and are vendored beside this script in vendor/, with their licences.
#
# Removing the venv also deleted a bug rather than working around it: pyautogui
# pulls `python3-Xlib` 0.15 from PyPI, which installs the same Xlib package
# directory as python-xlib 0.33 and shadows it, and the launcher used to
# uninstall and reinstall it ON EVERY START to repair that. Ubuntu's
# python3-pyautogui depends on the good python3-xlib, so the collision cannot
# arise.
PY="python3"
export PYTHONPATH="$PWD/vendor${PYTHONPATH:+:$PYTHONPATH}"

# Fail loudly and usefully rather than at the first import deep inside the app.
if ! "$PY" - <<'IMPORTS'
import sys
required = {
    "gi": "python3-gi", "PIL": "python3-pil", "numpy": "python3-numpy",
    "cv2": "python3-opencv", "pyautogui": "python3-pyautogui",
    "pyperclip": "python3-pyperclip", "requests": "python3-requests",
    "Xlib": "python3-xlib", "ewmh": "python3-ewmh",
    "mss": "(vendored)", "pytesseract": "(vendored)",
}
missing = []
for mod, pkg in required.items():
    try:
        __import__(mod)
    except ImportError:
        missing.append((mod, pkg))
if missing:
    print("[soc] missing Python modules:")
    apt = [p for _, p in missing if not p.startswith("(")]
    for mod, pkg in missing:
        print(f"[soc]   {mod}  <- {pkg}")
    if apt:
        print("[soc] install them with:")
        print("[soc]   sudo apt install " + " ".join(sorted(set(apt))))
    sys.exit(1)
IMPORTS
then
    exit 1
fi

# ── V plugin check (parity with run.bat) ─────────────────────────────────────
# Warn if no VLM server is reachable, but never block — Agent 4 simply stays
# offline. curl is not installed by default on this box, so this uses Python.
if [[ -f plugins/v_plugin.py || -f plugins/v_plugin/v_plugin.py ]]; then
    if "$PY" - <<'EOF'
import urllib.request, sys
for url in ("http://localhost:8080/v1/models", "http://localhost:8082/v1/models"):
    try:
        urllib.request.urlopen(url, timeout=2); sys.exit(0)
    except Exception:
        pass
sys.exit(1)
EOF
    then
        echo "[soc] VLM server reachable — V plugin will activate."
    else
        echo "[soc] No VLM server on :8080 or :8082 — Agent 4 (vision) offline."
        echo "[soc]   Load a model in GGUF Chatbox, or toggle V:off in Phase 1."
    fi
fi

# ── Syntax check (parity with run.bat) ───────────────────────────────────────
# The redirect goes to a temp file: `2> compile_err.txt` wrote into the working
# directory, which after install is root-owned /opt/baxters/... .
_err="$(mktemp)"
# compile() in memory, NOT py_compile: -B sets sys.dont_write_bytecode, but
# py_compile writes the .pyc EXPLICITLY and ignores it. Installed at
# /opt/baxters/soc-ultralight that directory is root-owned, so the check itself
# failed with "Permission denied: '__pycache__'" and the launcher reported
# "Compilation failed" -- the app never started. Found by a clean-VM install,
# 2026-09-25. compile() parses and writes nothing.
if ! "$PY" -c 'import sys; src=open(sys.argv[1], encoding="utf-8").read(); compile(src, sys.argv[1], "exec")' soc_ultralight.py 2>"$_err"; then
    echo "[soc] Compilation failed:"
    cat "$_err"
    rm -f "$_err"
    exit 1
fi
rm -f "$_err"

echo "[soc] Launching SOC Ultralight…"
exec "$PY" soc_ultralight.py "$@"
