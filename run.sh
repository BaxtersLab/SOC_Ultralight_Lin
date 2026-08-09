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
# It is FIRST because the venv bootstrap below calls `python3 -m venv`, and a
# snap-owned LOCPATH kills that interpreter outright, before SOC's own code
# runs at all:
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

PY="./.venv/bin/python"
if [[ ! -x "$PY" ]]; then
    echo "[soc] Creating virtualenv…"
    python3 -m venv --system-site-packages .venv
    "$PY" -m pip install --quiet --upgrade pip
    "$PY" -m pip install --quiet -r requirements.txt
fi

# ── python3-xlib shadow repair ───────────────────────────────────────────────
#
# pyautogui declares `python3-Xlib` on Linux. That is an ancient (0.15) fork
# which installs the SAME `Xlib` package directory as python-xlib (0.33), so
# whichever pip writes last wins — and on a fresh `pip install -r
# requirements.txt` it is the 0.15 one. 0.15 cannot do the modern Xauthority
# handshake, so the x11 backend dies with
#   Xlib.error.DisplayConnectionError: Can't connect to display ":0":
#   Authorization required, but no authorization protocol specified
# which reads like a display/permissions fault and is really a dependency one.
#
# Found 2026-08-09 by re-cloning the repo: the long-lived venv on this box had
# never installed either package (it inherited apt's python3-xlib 0.33 through
# --system-site-packages), so the bug was invisible until a clean checkout.
#
# Repaired on every launch, not just at creation — an existing venv may already
# be poisoned, and the check is a no-op once it is clean.
if "$PY" -m pip show python3-xlib >/dev/null 2>&1; then
    echo "[soc] Removing python3-xlib 0.15 — it shadows python-xlib 0.33…"
    "$PY" -m pip uninstall -y --quiet python3-xlib
    "$PY" -m pip install --quiet --force-reinstall python-xlib
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
if ! "$PY" -m py_compile soc_ultralight.py 2> compile_err.txt; then
    echo "[soc] Compilation failed:"
    cat compile_err.txt
    exit 1
fi
rm -f compile_err.txt

echo "[soc] Launching SOC Ultralight…"
exec "$PY" soc_ultralight.py "$@"
