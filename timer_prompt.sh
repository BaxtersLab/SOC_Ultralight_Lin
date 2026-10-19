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

# Same change as run.sh, for the same reason: this built a virtualenv and
# pip-installed into it on first start, which cannot work once installed --
# /opt/baxters/soc-ultralight is root-owned -- and reached the network at
# start-up, which A1 section 7.1 forbids. Dependencies now come from apt
# (declared in Depends), plus two pure-Python modules vendored beside this
# script.
PY="python3"
export PYTHONPATH="$PWD/vendor${PYTHONPATH:+:$PYTHONPATH}"

# The redirect goes to a temp file: `2> compile_err.txt` wrote into the working
# directory, which after install is root-owned /opt/baxters/... .
_err="$(mktemp)"
# compile() in memory, NOT py_compile: -B sets sys.dont_write_bytecode, but
# py_compile writes the .pyc EXPLICITLY and ignores it. Installed at
# /opt/baxters/soc-ultralight that directory is root-owned, so the check itself
# failed with "Permission denied: '__pycache__'" and the launcher reported
# "Compilation failed" -- the app never started. Found by a clean-VM install,
# 2026-09-25. compile() parses and writes nothing.
if ! "$PY" -c 'import sys; src=open(sys.argv[1], encoding="utf-8").read(); compile(src, sys.argv[1], "exec")' timer_prompt.py 2>"$_err"; then
    echo "[soc] Compilation failed:"
    cat "$_err"
    rm -f "$_err"
    exit 1
fi
rm -f "$_err"

echo "[timer-prompt] Launching…"
exec "$PY" timer_prompt.py "$@"
