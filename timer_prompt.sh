#!/usr/bin/env bash
# Timer Prompt — Linux launcher (counterpart to timer_prompt.bat).
#
# Same reasoning as run.sh, and it matters just as much here: the widget types
# into another application, and without SOC_PLATFORM set on a Wayland session
# pyautogui falls back to XTEST, whose keystrokes reach XWayland and no native
# Wayland client. The prompt would appear to be sent and land nowhere.
set -euo pipefail

cd "$(dirname "$(readlink -f "$0")")"

if [[ -z "${SOC_PLATFORM:-}" ]]; then
    if [[ "${XDG_SESSION_TYPE:-}" == "wayland" || -n "${WAYLAND_DISPLAY:-}" ]]; then
        export SOC_PLATFORM=wayland
    else
        export SOC_PLATFORM=x11
    fi
fi
echo "[timer-prompt] session=${XDG_SESSION_TYPE:-unknown}  backend=${SOC_PLATFORM}"

# VS Code's extension host leaks these into every terminal it spawns, and they
# follow child processes: GDK_BACKEND=x11 forces a Wayland app onto XWayland,
# ELECTRON_RUN_AS_NODE starts any Electron child as a bare Node runtime.
unset GDK_BACKEND ELECTRON_RUN_AS_NODE

PY="./.venv/bin/python"
if [[ ! -x "$PY" ]]; then
    echo "[timer-prompt] Creating virtualenv…"
    python3 -m venv --system-site-packages .venv
    "$PY" -m pip install --quiet --upgrade pip
    "$PY" -m pip install --quiet -r requirements.txt
fi

if ! "$PY" -m py_compile timer_prompt.py 2> compile_err_timer.txt; then
    echo "[timer-prompt] Compilation failed:"
    cat compile_err_timer.txt
    exit 1
fi
rm -f compile_err_timer.txt

echo "[timer-prompt] Launching…"
exec "$PY" timer_prompt.py "$@"
