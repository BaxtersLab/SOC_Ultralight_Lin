#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
# baxters-soc-ultralight — build the .deb from this tree.
#
# INSTALL DIRECTORY IS LOAD-BEARING. SOC Master Widget's packaged registry
# resolves this app as "../soc-ultralight", so renaming the directory silently
# removes two buttons from the cluster launcher. Asserted below.
#
# DISTRO interpreter lane (A1 s2a): needs gi, which has no PyPI wheel.
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")"
HERE="$PWD"

. "$HOME/workspace/baxters-repo/packaging/bxdeb.sh"

PKG=baxters-soc-ultralight
APPDIR=soc-ultralight
CONTROL="$HERE/packaging/DEBIAN/control"

VERSION="$(bx_control_version "$CONTROL")"
bx_assert_control "$CONTROL"

STAGE="$(mktemp -d)"; trap 'rm -rf "$STAGE"' EXIT
DEST="$STAGE/opt/baxters/$APPDIR"
mkdir -p "$DEST"

cp -a packaging/DEBIAN "$STAGE/DEBIAN"
cp -a packaging/usr/. "$STAGE/usr/"

# --- payload: read-only application code and data ---------------------------
bx_install_required "$HERE/run.sh"            "$DEST/run.sh" 0755
bx_install_required "$HERE/timer_prompt.sh"   "$DEST/timer_prompt.sh" 0755
bx_install_required "$HERE/soc_ultralight.py" "$DEST/soc_ultralight.py"
bx_install_required "$HERE/timer_prompt.py"   "$DEST/timer_prompt.py"
bx_install_required "$HERE/platform_layer"    "$DEST/platform_layer"
bx_install_required "$HERE/plugins"           "$DEST/plugins"
# vendor/ holds mss and pytesseract: pure Python, no Ubuntu package exists, and
# the alternative was pip-installing at start-up. Licences travel with them.
bx_install_required "$HERE/vendor"            "$DEST/vendor"
# The shipped template library. TEMPLATE_DIR now lives in the user's data
# directory, and soc_ultralight.py seeds it from here on first run -- without
# this the library would be empty on a fresh install and match nothing.
bx_install_required "$HERE/buttons database"  "$DEST/buttons database"
for item in LICENSE README.md THIRD_PARTY_LICENSES config.json; do
    [ -e "$HERE/$item" ] && bx_install_required "$HERE/$item" "$DEST/$item"
done
# SOP prompt files are read beside the script by _load_sop().
for sop in "$HERE"/*.txt; do
    [ -e "$sop" ] || continue
    bx_install_required "$sop" "$DEST/$(basename "$sop")"
done

# Icons, from the assets the app already ships.
for pair in "assets/soc-ultralight.png:$PKG:256x256" \
            "assets/timer-prompt-16.png:baxters-timer-prompt:16x16" \
            "assets/timer-prompt-24.png:baxters-timer-prompt:24x24" \
            "assets/timer-prompt-32.png:baxters-timer-prompt:32x32" \
            "assets/timer-prompt-48.png:baxters-timer-prompt:48x48" \
            "assets/timer-prompt-64.png:baxters-timer-prompt:64x64" \
            "assets/timer-prompt-128.png:baxters-timer-prompt:128x128" \
            "assets/timer-prompt-256.png:baxters-timer-prompt:256x256"; do
    src="${pair%%:*}"; rest="${pair#*:}"; name="${rest%%:*}"; dim="${rest##*:}"
    [ -e "$HERE/$src" ] || { echo "FATAL: missing icon $src" >&2; exit 1; }
    mkdir -p "$STAGE/usr/share/icons/hicolor/$dim/apps"
    cp -a "$HERE/$src" "$STAGE/usr/share/icons/hicolor/$dim/apps/$name.png"
done

find "$DEST" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true
find "$DEST" -name '*.bak' -delete 2>/dev/null || true

bx_write_mit_copyright "$STAGE/usr/share/doc/$PKG/copyright" "SOC Ultralight"
bx_normalise_modes "$STAGE"
chmod 0755 "$DEST/run.sh" "$DEST/timer_prompt.sh"
bx_assert_not_group_writable "$STAGE"
bx_assert_copyright "$STAGE" "$PKG"
for d in "$STAGE/usr/share/applications"/*.desktop; do
    bx_assert_desktop "$STAGE" "$d"
done

# --- assertions on the STAGED tree -----------------------------------------
[ -d "$STAGE/opt/baxters/soc-ultralight" ] || {
    echo "FATAL: install dir is not soc-ultralight -- breaks the SOC cluster registry" >&2; exit 1; }
# No launcher may pip-install or build a venv: it cannot work in a root-owned
# install directory and A1 s7.1 forbids reaching the network at start-up.
for sh in "$DEST/run.sh" "$DEST/timer_prompt.sh"; do
    if grep -nE '^[^#]*(pip install|python3 -m venv)' "$sh" >/dev/null 2>&1; then
        echo "FATAL: $(basename "$sh") still bootstraps a venv or calls pip" >&2
        grep -nE '^[^#]*(pip install|python3 -m venv)' "$sh" >&2
        exit 1
    fi
done
# The vendored modules must actually be importable from the payload.
( cd "$DEST" && PYTHONPATH="$DEST/vendor" python3 -B -c '
import mss, pytesseract
print("  vendored:", "mss", mss.__version__, "/ pytesseract", pytesseract.__version__)
' ) || { echo "FATAL: vendored modules do not import from the payload" >&2; exit 1; }
[ -s "$DEST/THIRD_PARTY_LICENSES" ] || { echo "FATAL: THIRD_PARTY_LICENSES missing" >&2; exit 1; }
for l in vendor/LICENSE.mss.txt vendor/LICENSE.pytesseract.txt; do
    [ -s "$DEST/$l" ] || { echo "FATAL: $l missing -- vendored code without its licence" >&2; exit 1; }
done
n=$( { find "$DEST/buttons database" -iname '*.png' -print 2>/dev/null || true; } | wc -l )
[ "$n" -gt 0 ] || { echo "FATAL: template library is empty -- a fresh install would match nothing" >&2; exit 1; }
echo "  template library: $n PNG(s)"

OUT="$HERE/dist/${PKG}_${VERSION}_all.deb"
bx_build_deb "$STAGE" "$OUT"
echo "built $OUT ($(du -h "$OUT" | cut -f1))"
