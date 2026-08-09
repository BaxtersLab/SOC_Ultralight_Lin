# handoffs — SOC Ultralight (+ V_plugin / A4v, Master Widget, GGUF-Chatbox)

_Append-only (Article VIII). Newest entry at the top._

## [2026-08-09] — **Repository rebuilt** to drop two desktop screenshots; re-cloning exposed a fresh-install bug that had been invisible for the whole port

Operator deleted the public repo and had this box push a sanitized rebuild.

### Why a rebuild rather than a file removal

`docs/images/desktop_layout_2agent.PNG` and `_3agent.PNG` were full 1920x1080
captures of the real screen — readable chat text across three windows, a file
listing, the taskbar and the account name. Removing them going forward would
have left them reachable in the old commits, so history was restarted at a
single clean commit (`08ae2d1`).

Kept deliberately: **`banner.png` is designed artwork, not a capture**, and the
**46 `buttons database` crops are assets** — SOC's template matching needs them
and they are UI fragments, largest 251x41. An earlier draft of the question
wrongly lumped banner.png in with the captures; corrected before pushing.

`README.md` embedded both captures, so removing the files would have left broken
images. Replaced with box-drawing diagrams of the 2- and 3-agent layouts, which
document what SOC actually needs visible (input field, send button, scroll
control) rather than a moment on someone's desktop. `.gitignore` now blocks
`docs/images/desktop_layout_*`.

### What the re-clone caught — the real find

The workspace was re-cloned from the new repo to guarantee no dirty history
survived locally. **The first `pytest tests/` on that fresh checkout could not
even collect:**

```
Xlib.error.DisplayConnectionError: Can't connect to display ":0":
Authorization required, but no authorization protocol specified
```

Cause: **pyautogui declares `python3-Xlib` on Linux** — an ancient (0.15) fork
that installs the SAME `Xlib` package directory as `python-xlib` (0.33). Both
were pulled in; whichever pip writes last wins, and on a clean
`pip install -r requirements.txt` that is 0.15, which cannot do the modern
Xauthority handshake.

**This had been broken for every fresh install for the entire port and nobody
could have seen it.** The long-lived venv on this box never installed either
package — it inherited apt's `python3-xlib 0.33` through
`--system-site-packages`, so every previous "153 passed" was run against a venv
that a fresh clone does not reproduce. It reads like a display or permissions
fault; it is a dependency one.

`run.sh` now removes the shadow and reinstalls `python-xlib`, on **every**
launch rather than only at venv creation, since an existing venv may already be
poisoned. No-op once clean. Commit `e1d810b`.

### Trap for the next session

**A green suite on this box is not evidence a fresh clone works.** The venv here
predates most of the port and silently supplies system packages that
`requirements.txt` would otherwise install badly. Re-clone into a scratch dir
and build the venv from nothing before believing an install path.

### Verification

* Sanitized commit audited before pushing: 0 objects matching `desktop_layout`,
  0 matching any gitignored runtime path, 1 commit, 131 files.
* Confirmed against the live GitHub API after pushing: `docs/images/` contains
  only `.gitkeep` and `banner.png`.
* **`.venv` deleted entirely and rebuilt by `run.sh`**: the repair fired, Xlib
  resolves to 0.33, `display.Display()` connects to `:0`, SOC's window mapped
  with `WM_CLASS soc-ultralight`, **153 passed, 19 subtests**.
* Calibration carried across the re-clone byte-identical (`config.json`, all
  agent sections). `buttons database/registry.json` differs only in match
  counters, which the verification run itself incremented — 39 template entries
  both sides.
* Desktop entry still resolves to an existing `run.sh`; Master Widget still
  reports SOC and Timer Prompt READY as siblings; launched through the
  `.desktop` entry.

### Cleanup

Old 12-commit workspace copy deleted after the above passed. The file-cabinet
copy carried a **stale `.git` pointing at the live remote with the captures in
its objects** — a force-push from there would have re-published them; removed,
and its files refreshed from the verified clone. The captures are gone from
workspace and file cabinet; they remain in the dated
`My Passport/file cabinet backup 8-6-25` snapshot, which was deliberately not
touched.

## [2026-08-08] — SOC's main window has **never** set an icon; mirror advisory written for the Windows agent

No SOC code changed here. Recording a finding and where the advisory lives.

**The main window sets no icon on Windows, and never has.** Exhaustive search of
the tree: `soc_ultralight.py` contains no `iconbitmap` call at all. The only
icon call anywhere is `plugins/v_plugin/v_plugin.py:475`:

```python
self._win.iconbitmap(default=str(_ico))     # a4v_icon.ico
```

That is the **`default=`** form, which on Windows sets the icon for *every*
toplevel in the process, including later ones. So on the Windows fork SOC's
main window shows either the **A4v plugin's** icon (if the plugin loaded) or
the bare **pythonw** icon (if it did not) — decided by plugin load order.
Neither is deliberate. The comment at `soc_ultralight.py:9220` says the
AppUserModelID is declared so Windows will "honour each window's iconbitmap",
but the main window has none to honour.

This only surfaced because the Linux side now sets `iconphoto` explicitly and
the asymmetry became visible. The Linux path is unaffected — `iconbitmap` is
Windows-only for `.ico` and stays behind an `os.name` check.

Not fixed here: the Windows entry point is in a different repo
(`BaxtersLab2/SOC_Ultralight`, a **different account** from this fork's
`BaxtersLab/SOC_Ultralight_Lin`), and per the ownership boundary that is the
Windows agent's tree. Written up for them instead, with the asset generated so
it is a drop-in rather than a task.

### Advisory location

`/run/media/baxter/USB321FD/soc ultralight mirror advisory/` — `INDEX.md`,
`SOC_MIRROR_ADVISORY.md`, `soc-ultralight.ico` (7 sizes, 16→256),
`soc-ultralight-256.png`, `generate_icon.py`. Separate from
`windows agent handoff/` (the 2026-08-04 port list); neither supersedes the
other, and they must not be merged.

Reminder recorded there and here: **`assets/ob_icon.ico` is the OUTBOX mark**,
not an app icon. It was standing in as one. `packaging/generate_icon.py` draws
the real "SOC" mark.

## [2026-08-07] — Snap scrub added to `run.sh` (it had none), and the block is now **executed** by a test rather than trusted

Follow-on to the entry below, at operator instruction. `run.sh` cleared only
`GDK_BACKEND` / `ELECTRON_RUN_AS_NODE`; it had no snap scrub, so starting SOC
from a VS Code terminal ran the venv bootstrap under a snap-owned `LOCPATH`.
Added the same block HRT and the Master Widget carry, placed **first** — before
`python3 -m venv`, since that is the interpreter `LOCPATH` kills.

**The scrub is shell, and shell was the one thing here nothing tested.** A typo
in it surfaces only as `symbol lookup error: undefined symbol:
__libc_pthread_init` at launch, which reads like a Python bug. So
`tests/test_run_sh_scrub.py` does not grep for variable names: it cuts the
shipped block out of `run.sh` between `# (scrub:begin)` / `# (scrub:end)`, runs
it under bash with a real contaminated environment, and asserts on what
survives. Same file added to the Master Widget, whose copy was equally untested
and higher-stakes — it spawns the other three apps, so its environment is
theirs.

**A test I wrote was asserting something false.** `test_no_variable_still_points
_into_a_snap` claimed nothing snap-pointing survives. The live launch showed six
survivors that are correct: `SNAP*` (inert strings), `VSCODE_NLS_CONFIG` (read
only by VS Code), and `PATH` — where `/snap/bin` is *wanted*, per CLAUDE.md's
"PATH needs no blanket removal". The test only passed because its fake
environment omitted them. Renamed to `..._no_loader_variable_...`, given an
explicit `INERT` allowlist with the reason each is inert, and its environment
extended to include the real survivors.

Also corrected the Master Widget's "leaks 21 of these" comment: 18 measured
today. The count moves with the extension set — it is now written as ~20 with
both measurements, not a fixed number.

### Traps hit

- **`text.index("python3")` found the word in the scrub's own comment**, which
  explains why it runs before `python3 -m venv`. The ordering test reported the
  block as coming after itself. It now strips comment lines first.

### Open Stubs

None introduced.

### Verification

* `pytest tests/` → **153 passed, 19 subtests** (140 before; +13 scrub tests).
* Master Widget `pytest` → **50 passed, 8 skipped** (37 before; +13).
* **Proven to fail first.** Against the pre-fix `run.sh` the suite gave 13
  errors (no markers → no block to execute).
* **Mutation-checked twice, since a scrub test that cannot fail is worthless.**
  Moving the block after the venv bootstrap → the ordering test fails alone.
  Dropping `GTK_IM_MODULE_FILE` from the explicit list → 2 fail. Its real value
  lives under `$HOME/snap/`, so it starts with `/home/` and **only** the
  explicit list catches it; the env-scanning loop never sees it. `run.sh`
  restored byte-identical after each (`diff` clean).
* **End-to-end under real contamination, which is the point of the change:**
  launched `./run.sh` from this session's VS Code shell (18 snap variables,
  `LOCPATH=/snap/code/254/usr/lib/locale`). SOC's window was up in ~1 s, and
  `/proc/<pid>/environ` shows all 9 fatal loader variables unset in the running
  interpreter, with `XDG_DATA_DIRS` filtered to 5 legitimate entries —
  `/var/lib/snapd/desktop` kept, no `/snap/` paths.

## [2026-08-07] — Desktop entry + a real app icon: SOC was unlaunchable from the shell and had no mark of its own

Operator reported SOC Ultralight and SOC Master Widget "do not have icons and a
way to start the app". Both were true, and for different reasons per app.

**SOC had no desktop entry at all.** `run.sh` existed (added 2026-08-03) but
nothing published it to the shell, so SOC appeared nowhere in the app grid — the
only way to start it was a terminal. Added `packaging/soc-ultralight.desktop`,
installed to `~/.local/share/applications/`. `Exec=` points at `run.sh`, never
`soc_ultralight.py`, for the backend-selection reason in the entry below.

**SOC had no icon of its own.** `assets/ob_icon.ico` is the **outbox** mark
("OB") — operator confirmed it was standing in, not the app icon. Added
`packaging/generate_icon.py`, which draws a "SOC" mark in the house style
measured off the sibling icons (tile `#010101`, glyph bbox 39,74–217,176 of
256², white ink; Master Widget's mark is blue on the same tile, so SOC stays
white to keep the two apart at dash size) and emits the 16–256 hicolor ladder.
`ob_icon.ico` is untouched and still the outbox's.

**Both apps are Tk, and Tk's default `WM_CLASS` is the generic `"tk"/"Tk"`** —
measured with `xprop`. GNOME matches a window to its `.desktop` through
`StartupWMClass`, so with the default class the shell cannot tell SOC from the
Master Widget and one steals the other's identity. Fixed at the source:
`tk.Tk(className="soc-ultralight")` in the entry point. Also set the window
icon there via `iconphoto` — `iconbitmap()` is Windows-only for `.ico` (X11
wants an XBM), which is why the existing call was a silent no-op on Linux.

### Traps hit

- **`xprop -root _NET_CLIENT_LIST` is not a reliable window census.** It missed
  SOC's window for 55 s while the window was mapped and healthy —
  `xwininfo -root -tree` found it immediately. Use the latter.
- **`xprop _NET_WM_ICON` prints empty by default**, which reads as "no icon".
  It is refusing to dump a 256² array, not reporting absence. Force the format:
  `xprop -id ID -len 40 -f _NET_WM_ICON 32c ' $0+\n' _NET_WM_ICON`.
- **`pkill -f soc_master_widget.py` killed the calling shell** (exit 144) — the
  pattern matches the wrapper's own command line. Same trap already documented
  for `Xvfb` at the bottom of this file; use the PID or the bracket form.

### Open Stubs

None introduced.

### Verification

* `pytest tests/` (venv interpreter) → **140 passed, 19 subtests passed**, 11.96 s.
* `desktop-file-validate soc-ultralight.desktop` → clean.
* `Gio.DesktopAppInfo.new("soc-ultralight.desktop")` under a **scrubbed** env →
  name/icon/exec/`wm_class=soc-ultralight`/`should_show=True` all resolve.
  Under this box's VS Code terminal env it returns NULL, because
  `XDG_DATA_HOME` points into `/home/baxter/snap/code/254/.local/share` — an
  18-variable snap leak, the documented contamination, not an install fault.
* Launched **through the desktop entry** (`gio launch`), not by hand:
  window `"SOC Ultralight"` mapped 250x796, `WM_CLASS = "soc-ultralight",
  "Soc-ultralight"` — matches `StartupWMClass`.
* Icon proven **live on the window**, not merely on disk: `_NET_WM_ICON` reads
  `256, 256, 4278255873, …`, byte-identical to the new PNG's ARGB pixels, and
  the process (13:33:33) postdates the icon file (13:32:59).
* Legibility checked by eye at 48 px, the dash size.

## [2026-08-03] — Auto-click root cause: **the Wayland backend was never selected at launch**

Operator reported auto-click still not working after the template fixes. The
templates were not the problem, and neither was the matching. **SOC was running
the x11 backend on a Wayland session.**

`platform_layer.get_platform()` deliberately does not auto-select the Wayland
backend — it requires `SOC_PLATFORM=wayland`, and `wayland_shims.install()`
gates on the same variable. There was **no Linux launcher in the repo** (only
`run.bat` / `run_isolated.bat`), and the Master Widget registry started SOC with
a bare `./.venv/bin/python soc_ultralight.py`. Nothing anywhere set the variable.
Every prior verification in this file was run with it exported by hand, which is
why the port looked finished while the shipped launch path was broken.

Measured on this box, same machine, same moment:

| launch | backend | desktop capture |
| --- | --- | --- |
| `python soc_ultralight.py` | `x11` | **mean 0.00, stdev 0.00 — pure black** |
| `SOC_PLATFORM=wayland …`   | `wayland` | mean 58.08, stdev 74.53 |

Template matching against a uniformly black frame matches nothing, forever, and
raises no error. `pyautogui`'s XTEST clicks go to XWayland, which the compositor
never forwards to native Wayland clients. The feature therefore ran, logged
nothing wrong, and did nothing — the worst available failure mode.

### Fixed

* **`run.sh`** (new) — picks the backend from the live session
  (`XDG_SESSION_TYPE` / `WAYLAND_DISPLAY`), respecting an explicit
  `SOC_PLATFORM` so `SOC_PLATFORM=x11 ./run.sh` still forces the old path. Also
  unsets `GDK_BACKEND` and `ELECTRON_RUN_AS_NODE`, which VS Code exports into
  every terminal it spawns, and carries over run.bat's V-plugin probe and
  `py_compile` check (using Python for the probe — curl is not installed here).
* **Master Widget registry** now launches SOC via `./run.sh`, with the reason
  recorded in the entry's note so it is not "tidied" back to a bare python call.
* **Loud startup banner** when the x11 backend is loaded on a Wayland session,
  on stderr, plus the same warning in the SOC log pane when the operator presses
  Scan — stderr is discarded when the Master Widget launches with
  `console: false`, which is exactly how this went unnoticed.
* **Blank-capture detection in `_autoclick_loop`.** If a captured frame has
  `stdev < 1.0` it is not a desktop; the loop now says so once per scan, with
  the measured mean/stdev, backend name and session type, instead of spinning
  silently. This catches the failure *condition* rather than a proxy for it, so
  it still fires if capture dies for some other reason.

### Verified

* `wayland_shims.install()` → True; `pyautogui.click`, `pyautogui.position` and
  `PIL.ImageGrab.grab` all rerouted to `platform_layer.wayland_shims`.
* Portal capture returns real pixels (mean 58.08) with the cached restore
  token — no permission prompt.
* `py_compile` clean; **156 passed, 19 subtests passed**.
* Master Widget: 37 passed, 8 skipped; all 5 registry entries resolve.
* NOT verified: an actual template click landing on a target window. That needs
  the operator to enable a template and watch it fire — the capture and
  injection paths are both proven, the end-to-end click is not.

## [2026-08-03] — **SOC runs on Wayland.** Full agent loop verified 11/11 on a native Wayland window; Set Win calibration UI replaced; 137 tests green

Commits `2dfe2df` → `359d2e7` → `58e81ae`, pushed to `BaxtersLab/SOC_Ultralight_Lin`.

### Done

**1. The Wayland backend is wired in under SOC's existing call sites (`2dfe2df`).**
SOC drives the desktop through pyautogui (33 call sites) and reads it through PIL.ImageGrab/mss
(8 more). None work on Wayland — pyautogui's backend is XTest, ImageGrab captures the X root
window, and under XWayland both see only XWayland clients, so they fail *silently* by clicking
into nothing and returning partial images. `platform_layer/wayland_shims.py` routes all 41
through the portal/PipeWire desktop stream, so **no call site changed**.

Install order is load-bearing: the shim installs **before** the `_hands_wrap` loop, so the hands
guard wraps the working functions. Installing after would have replaced the guarded functions
with unguarded ones and silently disabled the operator-yield rule.

**2. A full agent loop, end to end (`soc_port/verify_agent_loop.py`, 11/11).**
calibrate → locate (conf 1.000) → crop → OCR → click a window-relative point → type through the
pyautogui shim → **read the typed token back out of the window's own pixels**. Target was a
native Wayland window, asserted invisible to `xdotool` so the test cannot pass through XWayland
by accident.

**3. Calibration now verifies WHICH window it captured (`359d2e7`).**
The ScreenCast portal returns pixels and nothing else — no title, no app id, no pid — so
`calibrate()` was trusting compositor stacking order and saving the result as ground truth.
During bring-up the portal returned a *different* application's window; calibration accepted it,
and the click and keystrokes that followed went into an unrelated app (a sign-in form). A wrong
reference is worse than no reference: every later `locate()` and `click_at()` inherits it and SOC
clicks unattended.

`calibrate()` now takes `expect=` (OCR marker) and `confirm=` (preview callback). On rejection
nothing is saved, the **restore token is dropped too** — a token pointing at the wrong window
would silently restore it next run with no picker shown — and the refused capture is written to
`<agent>_rejected.png`, because "wrong window" with no way to see *which* cannot be told apart
from a marker that simply did not OCR, and those have opposite remedies.

**4. Set Win replaced on Wayland (`58e81ae`).**
The old flow hovered the cursor and read the window underneath. Neither half exists here: no
`cursor_pos()`, no `window_from_point()`. `_set_window` now dispatches — win32/X11 keep the hover
path **untouched**; Wayland uses the portal picker plus the preview-confirm dialog.

`ocr_region` stays screen-absolute (every consumer — `ImageGrab` bbox, `pyautogui.scroll` — takes
absolute coordinates) but on Wayland is re-derived from the tracked window origin plus a new
window-relative `rel_region`, so a **window the operator moves keeps working**. The refresh lives
in `_focus_agent`, which the six former `focus_window` callers now route through; that call
already preceded every interaction. Two of those six read `ocr_region` *before* focusing — that
ordering is now inverted, since focusing is what refreshes it.

`cfg.hwnd` holds the agent id on Wayland (documented as an opaque platform reference). Because
identity is a saved image rather than a session-scoped handle, **calibration survives a restart**
— restored only when the reference file exists, so a deleted one re-prompts.

### Remaining

- **`⊙ Input` / `⊙ Send` / scroll coordinate capture are still dead on Wayland**
  (`soc_ultralight.py` `_capture_coord` / `_do_capture`). They use the same hover + `cursor_pos()`
  approach Set Win used. Planned fix: let the operator click the target **on the captured window
  image**, which yields window-relative coordinates that follow the window — reusing the preview
  dialog already built. This is the last blocker to a fully calibrated agent on Wayland.
- Text-bearing templates still need re-capturing against Linux apps. Icon/arrow templates transfer
  (measured 0.995); text ones do not (DirectWrite vs FreeType).
- **Copilot Desktop login unresolved** — operator could not complete sign-in; not a SOC defect and
  not diagnosed further. Agent 1 could therefore not be calibrated against its real window.

### Decisions

- **Shim under pyautogui rather than rewriting 41 call sites.** SOC already treats pyautogui as a
  seam (`_hands_wrap`), so a backend swap underneath keeps the hands guard intact and leaves the
  Windows/X11 path byte-identical.
- **`position()` returns the last position SOC set, and says so.** Wayland has no protocol to
  query the pointer, so it cannot track the operator's physical mouse — nothing on Wayland can.
  SOC already copes (`install_input_hook` returns False and it falls back to its own watcher),
  which is why this is safe rather than quietly wrong.
- **`focus_agent` returns False honestly on Wayland.** No protocol can raise another client's
  window. Every caller proceeds regardless, which is correct here — clicks are aimed by locating
  the window visually, so they land on the right pixels focused or not.
- Anything needing another application's window is **not** shimmed (see
  `WaylandPlatform.UNSUPPORTED`); those callers must use `wayland_agents.AgentWindow`.

### Environment traps found (cost several hours; all confirmed)

- **`GDK_BACKEND=x11`** — VS Code's extension host exports it to every child, silently forcing GTK
  apps onto XWayland. A "native Wayland" test target launched from an agent shell is not native at
  all. Detect with `xdotool search --name`: a genuinely native window returns nothing.
- **`ELECTRON_RUN_AS_NODE=1`** — same origin; makes any Electron binary behave as plain Node and
  reject its own launcher's Chromium flags (`bad option: --no-sandbox`), which looks exactly like
  broken packaging.
- **Never launch a long-lived GUI app with stdout redirected from a tool-call shell.** The shell is
  torn down, the descriptor dies with it, and the app crashes on its next `console.log` with
  `EBADF: bad file descriptor, write` — indistinguishable from an app bug. Use
  `systemd-run --user --collect --unit=<name> <app>`.
- **NVIDIA 595 + Chromium** — Copilot Desktop rendered a white window until launched with
  `--disable-gpu`. Same driver family as the GTK4 Vulkan SEGV fixed earlier with `GSK_RENDERER=gl`.
  A user-level entry at `~/.local/share/applications/copilot-desktop_copilot-desktop.desktop`
  shadows the snap's and adds the flag; delete that one file to revert.
- **Piping a harness's stdout to `tail`/`grep` can hang forever** if it spawns a child that
  inherits the write end — a finished run looks stalled. Redirect to a file.

### Open Stubs

None introduced. `WaylandPlatform.UNSUPPORTED` is declared capability reporting, not a stub —
callers check `supports()`.

### Verification

- `pytest tests/ -q` → **137 passed** (3 new: the calibration marker guard).
- `soc_port/verify_agent_loop.py` → **11/11** on a native Wayland window.
- `soc_port/verify_wayland_shims.py` → **7/7**, proven by observed effect (typed text had to
  appear in a file written by the target window).
- `soc_port/parity_x11_nowm.py` → **19 passed, 0 failed** — X11 path unregressed.
- **Live UI check on the real desktop, SOC running under `SOC_PLATFORM=wayland`:** Set Win opened
  the portal picker; the preview dialog rendered the captured window; the operator cancelled a
  wrong window and the result was `window: not set`, **no reference written**, refused capture kept
  at `agent1_rejected.png`. The reject path is verified by the operator against a genuinely wrong
  window, not by a synthetic fixture.

## [2026-08-02] — **MAJOR: half the template library was invisible on Linux.** Case-sensitive `glob("*.png")` hid 20 of 40 templates. Fixed + regression-tested; 123/123 green

### The bug

```python
templates = list(TEMPLATE_DIR.glob("*.png"))     # 4 call sites
```

Windows filesystems are **case-insensitive**, so this also matches `Agent2_allow.PNG`. Linux ext4
is **case-sensitive** and it does not. **20 of the 40 shipped templates carry an uppercase `.PNG`
extension and were never loaded on this machine** — including the core workflow targets
`Send_message_to_Agent1.PNG`, `Send_message_to_Agent2.PNG`, `VS_code_allow.PNG`,
`keep_all_changes.PNG`, `Github_copilot_continue.PNG`, `Claude_chat_input_field.PNG`.

That is why auto-calibrate reported **"3/23 templates found"** — it was scanning barely half the
library and reporting the fraction as if the whole set had been considered.

Notably the author *was* aware of case elsewhere: the three `TEMPLATE_DIR.iterdir()` call sites
already guard with `p.suffix.lower() in (".png", ".jpg")`. Only the `glob()` sites were wrong.

### The fix

New `template_pngs(directory=None)` in `soc_ultralight.py` — `iterdir()` + `suffix.lower()`,
files only (so `_retired_blank/` is skipped), sorted case-insensitively, `OSError` → `[]`.
All 4 `glob("*.png")` call sites now route through it. **20 → 40 templates discovered.**

New `tests/test_template_discovery.py` (4 tests): uppercase `.PNG` discovered, mixed-case `.Png`
discovered, sub-directories skipped, missing directory returns `[]` rather than raising, and the
shipped library is fully visible. These **fail against the old `glob()`** and pass now.
**Suite: 123 passed / 0 failed** (was 119).

### This changes the earlier "templates need recapturing" conclusion again

Matching all 40 against a live Linux screen (`TM_CCOEFF_NORMED`, threshold 0.80):

| template | conf | previously |
|---|---|---|
| `Send_message_to_Agent2.PNG` | **0.995** | invisible |
| `send_message_to_Claude.PNG` | **0.967** | invisible |
| `agent2_scroll_dn.png` | 0.903 | 0.892 |
| `agent3_send.png` | 0.882 | 0.889 |
| `claude_positive_Indicator.PNG` | 0.821 | invisible |

**5 genuine matches, up from 2** — and the two strongest were among the hidden files.
Windows-captured templates scoring **0.995 on Linux** confirms the operator's point: the agent apps
are Electron (Copilot Desktop is literally an Electron wrapper around the Copilot *web* app), so
Chromium renders identical UI cross-platform. **Rendering was never the blocker; the glob was.**
Recapture is needed only for text-heavy templates, where DirectWrite vs FreeType genuinely differs.

### Fourth blank template retired

The case fix exposed one more degenerate template: `Agent1_chat_input_field.PNG` (217x40,
stdev 4.4 — a capture of an *empty* input field) matching flat screen at 0.915. Retired to
`_retired_blank/`. Lower severity than the scroll blanks: its role `chat_input_field` is not one of
the four core roles, so it polluted the training registry without filling a coordinate slot.
**39 active templates, all non-blank.**

### Why this was found at all

Diffing the working tree against a freshly fetched pristine upstream
(`BaxtersLab2/SOC_Ultralight`) to prepare the Linux fork. The uppercase `.PNG` files showed up in
the upstream listing and did not appear in a `*.png` glob of the local tree. **Worth doing before
any port: diff against pristine upstream and read the file listing, not just the code.**

## [2026-08-02] — Template audit: 3 blank templates retired (a real, platform-independent calibration bug); Windows templates DO partly transfer to Linux; one flaky test identified

### Windows-captured templates partly work on Linux — the earlier claim was too broad

An earlier entry said the `buttons database/` templates are Windows captures that "will need
re-capturing before calibration is meaningful". That over-generalised. **The agent apps are Electron
(VS Code, Copilot Desktop — the latter is literally an Electron wrapper around the Copilot *web*
app), so Chromium renders their UI from the app's own CSS, not native GTK/Qt widgets.** Icons and
button art are therefore near-identical across platforms.

Measured against a live Linux screen (`cv2.matchTemplate`, `TM_CCOEFF_NORMED`, `TEMPLATE_THRESH=0.80`):

- `agent2_scroll_dn.png` **0.892** and `agent3_send.png` **0.889** — genuine matches, on VS Code,
  which is not even the intended target app.
- Text-bearing templates (`*_input`, `*_send` with labels) scored **0.31–0.60** — font rendering
  (DirectWrite vs FreeType) is where the real divergence is, not widget art.

So the recapture burden is smaller than stated: icon/arrow templates likely transfer, text ones do not.

### DEFECT: three templates were blank, and blank templates match anything

| file | stdev | unique grey levels |
|---|---|---|
| `agent5_scroll_dn.png` | **0.0** | **1** (one solid colour) |
| `agent1_scroll_up.png` | 0.3 | 2 |
| `agent5_scroll_up.png` | 4.2 | 3 |

`TM_CCOEFF_NORMED` scores a featureless patch at ~1.0 against **any** flat screen region, so these
matched empty desktop at 0.89–1.00 — above threshold. **Three of five apparent calibration "hits"
were these false positives**, which is why an initial reading of "5/23 matched" was wrong; the
honest figure was 2 genuine matches.

Not harmless. `_apply_template_match` fills an empty slot:

```python
elif r == "scroll_up":
    if c.scroll_up_xy is None:      # only when unset
        c.scroll_up_xy = (px, py)
```

Manually set coordinates are protected, but on a **fresh calibration** (fields still `None` — the
state the GUI actually showed) a false match writes a bogus XY and SOC clicks there. **This misfires
on Windows too — it is not a port issue.**

Per the operator, these were captures of the *region* clicked to scroll, never load-bearing for
matching (the scroll action homes on a stored XY); they simply ended up in the template folder.

**Moved to `buttons database/_retired_blank/` with a `WHY_RETIRED.md`, not deleted** — this repo is
not under version control on the Linux box, so a delete would be unrecoverable. `TEMPLATE_DIR.glob("*.png")`
does not recurse, so nothing there is loaded. 20 active templates remain (16 core + 4 extra).

### Flaky test — fails under CPU load, not a regression

`tests/test_soc_core.py::HandsGuardTests::test_worker_thread_waits_until_operator_idle` failed once
("wrapped call never fired") while a CPU-heavy template match ran concurrently — the suite took
**7.19 s instead of the usual 2.79 s**. Three consecutive runs on an idle machine: **119/119 OK**.
The test is timing-sensitive and will misfire in CI or on a busy machine. Worth hardening (wait on
an event with a generous timeout rather than a fixed sleep) before it wastes someone's afternoon.

### Environment gap

**`git` is not installed on this box** (`nodejs`/`npm` are absent too). That is why source fetches
here used `wget` + tarball. `snapd` 2.76 *is* present.

## [2026-08-02] — **Injection VERIFIED for real: 17 passed / 0 failed / 7 unsupported.** uinput abandoned for the RemoteDesktop portal; corrects a false pass in the entry below

### Correction to the previous entry first

That entry reported `set_cursor_pos via uinput accepted (injected)` as a PASS. **It was a false
pass.** It asserted only that the write raised no exception. The pointer never moved. Corrected
count for that run: **16 passed, 1 unverified.** The real result is now 17, by a check that
actually observes the pointer.

This is the *third* instance in this codebase of "the call returned successfully and nothing
happened" — after `platform_x11.move_window` (EWMH ClientMessage silently dropped). Treat any
"returns True / did not raise" assertion in this platform layer as suspect until it observes an
effect.

### Why uinput was abandoned

`/dev/uinput` was writable, the writes succeeded, nothing moved. `/proc/bus/input/devices` showed:

```
N: Name="SOC Ultralight Virtual Pointer"
H: Handlers=mouse2 event17 js0     <- claimed by the JOYSTICK driver
B: REL=100                         <- REL_WHEEL only; no REL_X / REL_Y
B: ABS=3                           <- ABS_X | ABS_Y
```

An absolute-axis device carrying buttons but no relative axes is treated as a joystick, and
libinput will not drive the pointer from it. uinput *can* work with REL_X/REL_Y plus a
slam-to-origin trick, but relative motion goes through libinput's pointer acceleration, so a
requested (x, y) is not where the cursor lands — disqualifying, since SOC clicks exact coordinates
inside agent windows.

### The replacement: RemoteDesktop portal

`org.freedesktop.portal.RemoteDesktop` v2 — `NotifyPointerMotionAbsolute` in the compositor's own
coordinate space, plus `NotifyPointerButton`, `NotifyPointerAxisDiscrete`, `NotifyKeyboardKeysym`
(SOC needs keyboard injection to type into agents). New module `wayland_remotedesktop.py`; shared
portal plumbing factored into `wayland_portal.py`.

Absolute motion must be expressed against a stream, so **one session is both a RemoteDesktop and a
ScreenCast session**: `CreateSession` → `SelectDevices(POINTER|KEYBOARD)` →
`ScreenCast.SelectSources` *on the same session handle* → `Start`. One prompt covers both; the
`restore_token` is cached at `~/.cache/soc-ultralight/remotedesktop_restore_token`.

**Verified: 5/5 exact pixel matches**, pointer read back independently after each move.

### THE SCREEN LOCK INHIBITS ALL INJECTION — operational, not a bug

Every `RemoteDesktop.CreateSession` failed with `Session creation inhibited`, from the portal *and*
called directly on `org.gnome.Mutter.RemoteDesktop`. Cause, from gnome-shell's own code in
`libshell-18.so`:

```js
if (sessionMode.allowScreencast && _remoteAccessInhibited)
    remoteAccessController.uninhibit_remote_access();
else if (!sessionMode.allowScreencast && !_remoteAccessInhibited)
    remoteAccessController.inhibit_remote_access();
```

**When the screen locks, the shell enters a session mode with `allowScreencast` false and inhibits
remote access.** Confirmed live: `org.gnome.ScreenSaver.GetActive` was `true`, `LockedHint=yes`.
Unlocking fixed it instantly.

**This is a real deployment constraint for SOC: pointer and keyboard injection stop dead the moment
the screen locks**, which is exactly GNOME preventing background processes from typing into a
locked machine. SOC must detect this rather than fail silently — `org.gnome.ScreenSaver.GetActive`
is a cheap poll. The parity harness now reports it as UNSUPPORTED-because-locked instead of FAIL.

### Two verification instruments that lied — do not repeat them

1. **Capture-and-diff.** Park the cursor, grab a portal frame, diff. The PipeWire frames lagged the
   pointer, so frames showed the cursor at its OLD position and the diff found nothing. Looked
   exactly like "injection does nothing".
2. **XWayland readback over arbitrary points.** `xdotool getmouselocation` on `:0` only sees the
   pointer while it is over an **XWayland surface**. Probing the GNOME desktop or dock (native
   Wayland) returns the last known position, reading as a dropped motion. This produced a
   *perfectly alternating* 3/6 "every other motion is dropped" result that was pure artifact — the
   points that "worked" were inside a VS Code window (Electron/XWayland), the rest were not.
   Confining all probes inside one XWayland window gave **6/6**.

   Also: skip the `mutter guard window` when picking a window to probe. It is a full-screen
   XWayland overlay; a naive largest-window search selects it and then probes its corners, which
   are over native Wayland surfaces. That produced a bogus 1/5.

`verify_pointer_injection.py` implements the correct method and documents both traps in its
docstring. The flawed `verify_uinput_really_moves.py` was deleted rather than left to mislead.

### Result: 17 passed / 0 failed / 7 unsupported (of 24)

The 7 unsupported are the six window operations plus `cursor_pos`, all structural Wayland limits.
uinput remains in `platform_wayland.py` only as `uinput_status()` diagnostics; injection routes
through the portal.

## [2026-08-02] — **`platform_wayland.py` built** — 15 passed / 0 failed / 9 unsupported-by-platform. X11 backend still 19/19, core suite still 119/119

### What was built

| File | Role |
|---|---|
| `platform_layer/platform_wayland.py` | the backend — portal capture, uinput injection, D-Bus geometry |
| `platform_layer/wayland_screencast.py` | ScreenCast session held open across grabs |
| `platform_layer/__init__.py` | opt-in dispatch via `SOC_PLATFORM=wayland` |
| `~/workspace/soc_port/parity_wayland.py` | the same contract, three verdicts |

**Run it:** `SOC_PLATFORM=wayland python3 parity_wayland.py`

### The dispatch default was deliberately NOT changed

`get_platform()` still returns the **x11** backend on a Wayland session unless
`SOC_PLATFORM=wayland` is set. Auto-selecting Wayland would silently break window
targeting for every caller, because the Wayland backend cannot do window find/focus/move at all
while the x11 backend still does them for XWayland clients. The switch stays explicit until the
caller-side port (window handles → portal tokens) is done. Reversible; nothing currently passing
was disturbed — **re-verified: X11 parity 19/19, core suite 119/119 after the change.**

### Result: 15 passed, 0 failed, 9 unsupported (of 24 checks)

The harness reports **three** verdicts, not two. Counting UNSUPPORTED as PASS would be a lie;
counting it as FAIL would imply a fixable bug. Neither is true.

**PASSED (15):** backend identity; `find_windows` returns `[]` honestly; `is_window(bogus)` False;
`UNSUPPORTED` set declared; `supports()` accurate; `virtual_screen` from the compositor
`(0,0,1920,1080)`; `left_button_down` False; `install_input_hook` False; instance lock acquire +
second-instance blocked; `hide_own_console` / `set_app_id` safe no-ops; **`grab_screen` returns a
887 KB PNG in 1219 ms cold / `screen_changed()` 170 ms per poll**; tesseract present.

**UNSUPPORTED — structural, 7 of 9.** Wayland has no cross-client window API. Verified on this box,
not assumed: `org.gnome.Shell.Introspect.GetWindows` exists but returns **"Access denied"** to
ordinary callers (GNOME allowlists it), while the `ScreenSize` *property* on the same interface is
readable — which is what `virtual_screen()` now uses. No protocol exists to query the global pointer
either, so `cursor_pos()` is unsupported too.

    find_windows · window_from_point · focus_window · get_window_rect
    is_window · move_window · cursor_pos

**UNSUPPORTED — fixable, 2 of 9.** These are permission/package gaps, not platform limits.
**Both operator commands were run on 2026-08-02; state is now 16 passed / 0 failed / 8 unsupported:**

- `clipboard round-trip` — **RESOLVED.** `wl-clipboard` installed; round-trip passes.
  (`xclip` only ever reached XWayland.)
- `set_cursor_pos via uinput` — **udev rule installed and correct.** `/dev/uinput` is now
  `root:input 0660` (was `root:root 0600`) and `baxter` is in `input` per `/etc/group`. Blocked
  only because **this login session predates the group grant**, so its processes do not carry the
  group. **Ubuntu 26.04 ships neither `sg` nor `newgrp`**, so there is no way to pick a group up
  without a new session — it needs a real log out / log in, after which this becomes
  **17 passed / 7 structurally unsupported**.

`uinput_status()` distinguishes these two cases explicitly: "rule missing, here is the setup" vs
"setup is already correct, just log out". Reporting the first when the second is true sends people
chasing work that is already done.

### Design notes worth keeping

- **Article VII §2 compliance:** the six window methods are *declared* in
  `WaylandPlatform.UNSUPPORTED`, return their documented "nothing" value, and log once. They are not
  silent stubs, and `supports(name)` lets callers branch. A backend that faked success here would be
  far worse than one that admits the gap.
- **`virtual_screen()` deliberately avoids GTK/GDK.** Calling `Gtk.init()` just to read monitor
  geometry brings up a GL context, which crashes against NVIDIA 595.58.03 on this box — it did,
  mid-session, writing a 5.1 MB crash file and popping an apport dialog on the operator's screen.
  GDK also answers with **XWayland's** view (it selected `X11Display`), which is the wrong answer.
  The D-Bus property is both safer and more correct.
- **uinput is written with raw ctypes/struct ioctls**, so the backend needs no extra Python package.
  `python3-evdev` is used only if present, for `left_button_down`.
- **`left_button_down()` returns False when it cannot observe physical buttons.** That is the safe
  direction for SOC's attribution logic — it only ever suppresses automation, never triggers it.
- The capture session is **held open** across grabs (handshake ~94 ms, frame ~50 ms); re-handshaking
  per poll would be absurd. SOC gets its own token cache at
  `~/.cache/soc-ultralight/screencast_restore_token` — deliberately *not* shared with the throwaway
  proof script, so the app does not inherit a test harness's grant.

### Operator setup — DONE 2026-08-02

```sh
# 1. uinput injection — a PERMISSIONS change, never display config
echo 'KERNEL=="uinput", GROUP="input", MODE="0660", OPTIONS+="static_node=uinput"' \
  | sudo tee /etc/udev/rules.d/99-soc-uinput.rules
sudo usermod -aG input $USER
sudo udevadm control --reload-rules && sudo udevadm trigger

# 2. native Wayland clipboard
sudo apt-get install --no-install-recommends wl-clipboard
```

Both ran successfully. **Only a log out / log in remains** to activate the `input` group.
Reversible: `sudo rm /etc/udev/rules.d/99-soc-uinput.rules`, `sudo gpasswd -d $USER input`.
`WaylandPlatform.uinput_status()` prints the setup only when it is genuinely missing.

### Harness trap: never pipe these gates into `grep`/`tail`

`wl-copy` daemonizes to serve the clipboard selection and **inherits the pipe's write end**, so the
reader never sees EOF and the run appears to hang long after the script exited (it burned a 3-minute
timeout here). `xclip` does exactly the same thing in the X11 gate. **Redirect to a file, then read
the file.** Also do not clean up with `pkill -f "Xvfb :99"` — that pattern matches the wrapping
shell's own command line and kills the caller; use `pkill -f "[X]vfb :99"`.

## [2026-08-02] — **Wayland capture path PROVEN.** The Xorg question is settled: GNOME 50 has no X11 session to switch to, and the portal replacement works end to end

### The Xorg question is closed — it is not a config problem

Earlier entries treated the missing login-screen gear as "this box is configured Wayland-only."
That was too generous. Verified today:

```
gnome-shell --help   (GNOME 50.1)
    --wayland     Run as a wayland compositor
    --no-x11      Run wayland compositor without starting Xwayland
    --headless    Run as a headless display server
                  ... there is no --x11
apt-cache policy gnome-session-xsession   ->   no candidate; package does not exist
```

**GNOME 50 cannot run an X11 session at all.** The gear is not missing — the feature was removed
upstream. The previous install was not destroyed by a risky edit that went wrong; GDM was pointed
at a session that does not exist as software, so failure was certain.

**Stop planning around an Xorg session. There will never be one.** The prior entry's suggestion to
install `gnome-session-xsession` was wrong and is retracted — no such package exists on 26.04.

### The replacement is proven working, not theoretical

`xdg-desktop-portal` ScreenCast **v5** → PipeWire → GStreamer `pipewiresrc` → tesseract. This is
the same path OBS uses on Wayland (`obs-pipewire` is a portal client; OBS is not a separate
mechanism). Proof scripts, standalone and touching nothing in SOC:

- `~/workspace/soc_port/wayland_capture_proof.py` — full chain, `--fresh` re-prompts
- `~/workspace/soc_port/wayland_capture_bench.py` — timing budget

**First run** shows the GNOME picker once; the operator clicked, and it OCR'd 286 characters of
real screen text. **Second run reconnected silently in 1.245 s total with no dialog**, using the
cached `restore_token` (`persist_mode=2`). Portal reports `AvailableSourceTypes = 7` —
MONITOR | WINDOW | VIRTUAL — so **single-window capture is supported**, which maps onto SOC's
per-agent model.

### Measured budget (1920x1080)

| stage | cost |
|---|---|
| silent handshake (once) | 94 ms |
| frame grab | **50 ms** (19.8 fps sustained) |
| full-frame hash | 13 ms |
| tesseract, full screen (165 chars) | ~400 ms |
| tesseract, dense 960x540 region (1042 chars) | ~780 ms |

**Capture is not the bottleneck — tesseract is ~89% of the cost, and would be identical on X11.**

**Counterintuitive, and it invalidates the obvious optimisation:** tesseract's cost tracks *how much
text it finds*, not pixel count. Cropping to a smaller but denser region was **slower** (781 ms for
a quadrant vs 428 ms for the whole screen). So configuring SOC's `ocr region:` field to speed things
up does not work. PSM/OEM tuning is marginal (`--psm 6` + grayscale: 788 → 676 ms, ~14%).

**The lever that does work is change detection:** hash the frame, skip OCR when nothing moved. A
static poll costs **~63 ms** (grab + hash) with no tesseract at all. SOC's 1.5 s normal mode fits
easily; **0.3 s rapid mode is reachable in the steady state** because most polls hit an unchanged
screen — you pay OCR only on frames that actually changed.

### What portals do NOT provide — the real porting cost

No cross-client window enumeration, no focus/restore, no window handles. Wayland has no such API
**by design**, and it will not be added. SOC's per-agent config is literally *window handle + input
field XY + send button XY*, which is X11-shaped. The port replaces:

- window handle → **per-agent portal restore token**
- global screen XY → **coordinates relative to that window's stream**
- pyautogui/XTest injection → **`uinput`** (sits below the display server, works on X11 *and*
  Wayland, no per-action prompt; `/dev/uinput` is root-only here and would need a udev rule —
  a permissions change, **not** a display-server change, but still needs operator consent)

`platform_layer/` is already the right abstraction for this: a `platform_wayland.py` sibling to
`platform_x11.py`, with the 19-check parity gate as the contract it must satisfy. **Added backend,
not a rewrite.** The X11 work in the entry below is not wasted — it stays as the legacy backend and
defines the contract.

### Traps

- `grim`, `slurp`, `wf-recorder` install cleanly from the archive and **do not work on GNOME** —
  they use wlroots-only `wlr-screencopy`, which Mutter does not implement.
- **Wine is a dead end for this class of app.** Win32 capture/injection under Wine maps to X11,
  which on this box means XWayland — sandboxed, able to see only other XWayland clients, never
  native Wayland windows or the real desktop. Fine for ordinary apps; useless for anything whose
  job is capturing or driving the host desktop.
- Portal calls are async: subscribe to the `Response` signal on the predicted Request path *before*
  calling, or you race a fast reply.

## [2026-08-01] — Linux intake on Ubuntu 26.04 / Python 3.14: core suite green, **X11 parity 19/19**, both llama slots proven on real hardware; 5 defects found and fixed; live-desktop loop **UNVERIFIED and deliberately blocked**

### Scope

SOC is not one repo. This entry covers the four that make up the suite on this box:

| Repo | Role | Location |
|---|---|---|
| `SOC_Ultralight` | core + `platform_layer` seam | `~/workspace/SOC_Ultralight` |
| `V_plugin` | **A4v** vision component | vendored to `SOC_Ultralight/plugins/v_plugin/` |
| `SOC_Master_Widget` | launcher / app registry | `~/workspace/SOC_Master_Widget` |
| `GGUF-Chatbox` | model-serving dependency | `~/workspace/GGUF-Chatbox` |

Fetched from GitHub as archives, **not** git clones — so there is no `git diff` to review. Every
change listed below was re-verified as present in the files at the end of the session.

### Gates — what actually passed

- **Core suite: 119 passed / 0 failed** (`python -m unittest discover -s tests`).
  Also **135 passed / 0 failed** under `pytest -q`, which collects test files outside `tests/`.
  Both figures are real; they differ only in collection scope.
- **X11 parity: 19 passed / 0 failed — exact match to the Ubuntu 25.04 podman baseline.**
  Run via `~/workspace/soc_port/parity_x11_nowm.py` under `Xvfb :99` at 1920x1080.
- **A4v verified** — `v_plugin` reports `PLATFORM=x11`; the `soc_show_a4` signal write works.
- **Master Widget: 33 passed / 9 skipped / 0 failed.** The 33 is the *pre-existing* pass count,
  preserved exactly — see the guard-scoping note under Defects.
- **Both llama slots serve on real hardware:** `llama-server` on **8080 (A5, text)** and
  **8082 (A4, vision)**, each correctly answering `2 + 2 = 4`.
- **GGUF-Chatbox: 7 library crates build** (`logging`, `error_system`, `adaptive_llama`,
  `tool_belt`, `server`, +2), 1 warning — unused `std::io::Read` in `crates/server/src/mcp.rs`.

### The Xorg-session finding — READ THIS BEFORE TOUCHING DISPLAY CONFIG

SOC's documented requirement is an **Xorg session** (Wayland breaks the OCR/inject stack; the module
docstring in `platform_layer/platform_x11.py` says "Xorg ONLY").

**This box cannot satisfy that requirement, and must not be made to.**

- `/usr/share/xsessions/` **does not exist**. `gnome-session-xsession` is not installed.
  Only `/usr/share/wayland-sessions/ubuntu.desktop` is present. The install is **Wayland-only**.
- Therefore **there is no gear icon at the GDM login screen** — there is no Xorg entry to select.
- On the previous install, an agent hit exactly this wall and **embedded a startup override to force
  Xorg instead of Wayland**. GNOME was fatally corrupted; the operator lost the desktop, the session,
  and ultimately the whole OS. It was reinstalled 2026-07-31.

**The trap is live again on this install.** Do not edit `/etc/gdm3/custom.conf`, `WaylandEnable`,
AccountsService session keys, or any session `.desktop` file. Do not script a session switch.

What to do instead, in order:
1. **Use `Xvfb`** — an isolated X server on `:99` satisfies X11 clients completely and touches
   nothing on the real desktop. **This is how the 19/19 parity gate above was proven.** It covers
   verification, which is nearly always what is actually needed.
2. If a real Xorg *session* is genuinely required, the supported route is installing the package
   Ubuntu ships for it (`gnome-session-xsession`), which **adds** a login-screen entry without
   changing the default — and then **the operator picks it by hand**. Disclose contents, get consent.
3. **Never** force it in config.

### The window-manager blocker, and how it was closed

The parity gate's EWMH checks (focus, stacking, move) need a window manager. `openbox` was the
container baseline, but **openbox ships `/usr/share/xsessions/openbox.desktop` and `/usr/bin/gdm-control`**
— i.e. it puts an entry on the login screen. Given the history above, that was rejected.

Seven packaged WMs were downloaded with `apt-get download` and inspected **without installing**:

| Package | Adds `/usr/share/xsessions/` entry | GDM tooling | Verdict |
|---|---|---|---|
| evilwm, fluxbox, herbstluftwm, icewm, jwm, twm | **yes (1 each)** | none | rejected |
| **matchbox-window-manager** | **none** | none | **CLEAN — installed** |

`matchbox-window-manager` is built for embedded/kiosk use and ships no session file. Its entire
footprint: `/usr/bin/matchbox-window-manager`, `/usr/bin/matchbox-remote`, `/etc/matchbox/kbdconfig`,
themes, man pages. 2 packages installed (`+libmatchbox1`), 0 removed.
**Post-install re-check confirmed `/usr/share/xsessions/` still does not exist.**
Reversible: `sudo apt remove matchbox-window-manager libmatchbox1`.

Matchbox defaults to kiosk mode (every window forced fullscreen), so the parity harness launches it as:

```
matchbox-window-manager -use_titlebar yes -use_dialog_mode free -force_dialogs "SOC Parity Target"
```

which restores the free-floating configure/move semantics openbox gives by default.

### Defects found and fixed (5)

1. **`platform_layer/platform_x11.py` — `move_window()` silently did nothing and returned `True`.**
   It sent `_NET_MOVERESIZE_WINDOW` via EWMH with a `try/except` fallback to a direct
   `win.configure()`. But `_NET_MOVERESIZE_WINDOW` is a **ClientMessage**: a WM that doesn't
   implement it drops the request silently rather than raising — so **the author's fallback was
   unreachable by construction**, and the call reported success while the window never moved.
   Proven, not inferred: matchbox advertises only `_NET_WM_WINDOW_TYPE_TOOLBAR` in `_NET_SUPPORTED`,
   the EWMH move was a no-op, and a plain `XMoveWindow` on the same window moved it (104,120 →
   304,220). Fixed by confirming the move landed (`_moved_near`, `_FRAME_TOL = 40` to allow for
   reparenting WM frame offsets) and issuing the direct `configure()` when it didn't. This turned
   parity from 18/19 to **19/19**, and the core suite stayed green (119/119).
   *Not merely a matchbox quirk — the same silent-success bug would hit any WM lacking the atom.*
2. **`requirements.txt` — `pywin32` was unconditional**, so a plain `pip install -r` could never
   resolve on Linux. Now `pywin32; sys_platform == "win32"`, plus `python-xlib` and `ewmh` guarded
   to `sys_platform == "linux"`.
3. **`SOC_Master_Widget/test_master_widget.py` — Windows-only tests failed on Linux.** Guarded with
   `@unittest.skipUnless(sys.platform == "win32", ...)`, and the registry test now `skipTest`s when
   `soc_master_apps.json` is absent instead of failing. **Guards are method-level, not class-level:**
   a first attempt put a class-level guard on `VSCodiumDependencyTests`, which would have skipped
   12 tests when only 3 were platform-bound — silently dropping 9 that were passing. Reverted, so
   the pre-existing 33-pass count is preserved exactly.
4. **`GGUF-Chatbox/probe_llm.py` hardcoded `http://127.0.0.1:8080` and the model name** — it could
   therefore **never probe the A4 vision slot on 8082 at all**. Now `PROBE_BASE` / `PROBE_MODEL`
   env overrides with the old values as defaults (Article XI).
5. **`soc_port/parity_x11_nowm.py` hardcoded `/soc`** — replaced with a `SOC_ROOT` env override
   falling back to a path relative to the script, and the WM launch made optional + WM-agnostic.

### Also resolved

- **llama symlink warning CLOSED.** `READ_ME_FIRST` warned that symlinks were lost in transfer —
  true, because the transfer drive is **FAT32, which cannot store them**. Extracting `llama.tar.gz`
  onto ext4 restored **all 10 natively**. Nothing had to be recreated by hand.

### GUI VERIFIED — SOC Ultralight runs and renders on Linux

Launched on the isolated `Xvfb :99` (never on the real desktop) with the venv interpreter.
**Two windows come up and render correctly:**

- **`SOC Ultralight`** (250x812) — the setup panel: Agent 1 / Agent 2 window+input+send rows,
  Agent 3 and Agent 5 shown `[bypassed]`, Auto-Calibrate / Re-calibrate, Test Inject,
  Test Round-trip, Roll Call (A1/A2/A3/A4), Plan Project, Jump In, Auto Accept, Diagnostics/OCR.
- **`Agent 4 · Vision`** (1912x1056) — the A4v pane, banner `Agent 4 ready. Ctrl+Enter = send with
  live screenshot. Shift+Enter = text only.`, routing buttons `→ A1 / → A2 / → A3`, Region
  controls, Autonomous Scope, and `Send + Screenshot` / `Send (text only)`.

Screenshot captured via `mss` and inspected — this is confirmed rendering, not just a live process.
**Tkinter 8.6** in the venv; `pyautogui`, `pytesseract`, `mss` all import.

**No memory leak:** RSS held at **239,344 → 239,360 kB across 30 s** (+16 kB, noise) with 19 threads
stable. (Worth stating explicitly because the Iris GUI in this same migration leaked ~27 MB/s.)

### UNVERIFIED — do not read the gates above as covering these

- **The live A1 loop — OCR reading the operator's real screen and injecting into real agent
  windows — has NOT been exercised.** The GUI construction, the platform seam (19/19) and the model
  slots are each proven independently; the end-to-end loop driving the operator's actual desktop is
  not. It needs a real Xorg session, which — per the finding above — **does not exist here and must
  not be forced.** Blocked pending an operator decision.
- **The 23 button templates in `buttons database/` are Windows screen captures.** Auto-calibrate on
  startup reported `Calibrated: 3/23 templates found`. That number is *not* a defect — it was
  matched against an empty Xvfb desktop with no agent apps running — but the templates are pixel
  crops of Windows UI chrome and **will need re-capturing against the Linux apps** before calibration
  means anything here. That can only be done on the real desktop with the real agent windows open.
- **GGUF-Chatbox Tauri GUI shell does not build.** Missing dev libs: `webkit2gtk-4.1`,
  `javascriptcoregtk-4.1`, `libsoup-3.0`. The 7 library crates build fine; the shell is untested.
- Parity was run under **Xvfb + matchbox**, not under GNOME/Mutter. Behaviour on the real
  compositor is not covered by these numbers.

### Reproducing the parity gate

```sh
Xvfb :99 -screen 0 1920x1080x24 -nolisten tcp &        # 1920x1080 matters: a check asserts it
cd ~/workspace/soc_port
DISPLAY=:99 ~/workspace/SOC_Ultralight/.venv/bin/python parity_x11_nowm.py
```

Three traps that cost time here, all harness-side rather than SOC bugs:

- **Use the venv interpreter.** `mss` lives in `SOC_Ultralight/.venv`, not in system Python. Running
  under `/usr/bin/python3` fails the `mss region grab` check for the wrong reason.
- **Redirect to a file; do not pipe to `tail`.** The clipboard check spawns `xclip`, which
  daemonizes and inherits the pipe's write end — so `tail` never sees EOF and the run *looks* hung
  long after the script has already exited.
- **Do not clean up with `pkill -f "Xvfb :99"`** — that pattern matches the wrapping shell's own
  command line and kills the caller. Use the bracket form: `pkill -f "[X]vfb :99"`.

### Environment

Ubuntu 26.04, Python 3.14, NVIDIA 595.58.03, Wayland-only GNOME. Deps installed via **apt where
available** (system Python is PEP 668 `EXTERNALLY-MANAGED` and gnome-shell depends on it — pip into
system Python can break the desktop). `SOC_Ultralight/.venv` carries the rest.
