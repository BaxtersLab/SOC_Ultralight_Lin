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
