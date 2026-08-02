"""Per-agent window sessions — the Wayland replacement for window handles.

THE PROBLEM
-----------
SOC's per-agent config on Windows/X11 is:

    window handle + input-field XY + send-button XY   (absolute screen coords)

Wayland has no cross-client window API, so there is no handle to store
(`org.gnome.Shell.Introspect.GetWindows` returns "Access denied"), and absolute
screen coordinates break the moment the operator moves a window.

WHY THIS IS A HYBRID (measured, not assumed)
--------------------------------------------
The obvious design — one RemoteDesktop session per agent window, giving both
capture and clicks in window coordinates — is NOT possible on GNOME:

    whole-screen session : source_type=1 (MONITOR), devices=3 (KEYBOARD|POINTER)
    per-window session   : source_type=2 (WINDOW),  devices NONE
                           -> "Session is not allowed to call NotifyPointer methods"

That is coherent rather than a bug: a pointer is global to the compositor, so
"inject into a window" has no meaning — the cursor is not confined to one.
GNOME therefore grants input devices only to full-screen sessions.

So the two halves come from different places:

    capture   per-agent ScreenCast session, source_type=WINDOW.
              Frames contain only that window, so OCR and template matching
              work in window-relative coordinates and survive the window moving.
    input     the single shared whole-screen RemoteDesktop session, which needs
              ABSOLUTE screen coordinates.

The bridge is `locate()`: correlate the window's own capture against a
full-screen capture with cv2.matchTemplate to recover the window's on-screen
origin. Wayland will not tell us where a window is, but we can see where it is.

    s = agent_session("agent1")     # prompts once, then silent
    png = s.capture_png()           # just agent1's window
    s.click_at(120, 480)            # window-relative; re-locates if the
                                    # window moved

NOTE ON source_type
-------------------
Never infer "is this a window?" from the stream size — a MAXIMIZED window
streams at the full screen resolution and is indistinguishable from a monitor by
size. Use `is_window_scoped`, which reads the portal's own `source_type`.
"""

from .wayland_remotedesktop import BTN_LEFT, RemoteDesktopSession
from .wayland_screencast import CURSOR_HIDDEN, SOURCE_WINDOW, ScreenCastSession

#: Agent ids SOC calibrates. agent4 (A4v, vision) is deliberately absent — it is
#: not driven by click targets, and soc_ultralight's _apply_template_match
#: likewise only handles agent1/2/3/5.
AGENT_IDS = ("agent1", "agent2", "agent3", "agent5")

#: Minimum correlation for a window locate to be believed.
LOCATE_THRESH = 0.75
#: Side of the textured patch taken from the window to search the screen for.
#: Small enough that matchTemplate stays fast, large enough to be unambiguous.
PATCH = 160

_sessions = {}
_screen = None


def screen_session():
    """The shared whole-screen session that owns pointer/keyboard injection.

    One session, not one per agent: GNOME grants input devices per session and
    there is nothing window-specific about them.
    """
    global _screen
    if _screen is None:
        _screen = RemoteDesktopSession()
    return _screen


class AgentWindowSession:
    """Window-scoped capture for one agent, plus screen-space clicking."""

    def __init__(self, agent_id):
        self.agent_id = agent_id
        # Capture ONLY. Requesting input here is what GNOME refuses, and asking
        # for it would fail the whole session rather than just the clicks.
        self._cap = ScreenCastSession(
            source_types=SOURCE_WINDOW,
            token_name=f"{agent_id}_window_token",
            # Keep the pointer out of captured frames: these feed
            # cv2.matchTemplate, and a cursor parked over a button alters the
            # very pixels being scored against that button's template.
            cursor_mode=CURSOR_HIDDEN)
        self._origin = None          # (x, y) of the window on screen
        self._origin_conf = 0.0

    # ── capture ──────────────────────────────────────────────────────────────

    def capture_png(self):
        """PNG of this agent's window only (not the desktop)."""
        return self._cap.grab_png()

    @property
    def buffer_size(self):
        """Size of the PipeWire buffer — the MONITOR size, not the window.

        GNOME delivers a window stream in a monitor-sized buffer with the
        window drawn at (0,0) and the remainder black-padded. Measured: a
        1200x800 window arrived in a 1920x1080 frame, 46% non-black. So
        `stream_size` is useless as a window size; use `window_size`.
        """
        return self._cap.stream_size()

    @property
    def window_size(self):
        """Actual (w, h) of the window content inside the padded frame."""
        b = self.content_bounds()
        return (b[2] - b[0], b[3] - b[1]) if b else None

    def content_bounds(self, png=None, threshold=8):
        """(x0, y0, x1, y1) of the non-padding region of a captured frame.

        The window sits at the top-left, so x0/y0 are normally 0 and this
        yields the window's true size — which the portal does not report.
        """
        import numpy as np
        from PIL import Image
        import io
        a = np.array(Image.open(io.BytesIO(png or self.capture_png())).convert("L"))
        mask = a > threshold
        cols, rows = mask.any(axis=0), mask.any(axis=1)
        if not cols.any() or not rows.any():
            return None
        x0 = int(np.argmax(cols)); x1 = int(len(cols) - np.argmax(cols[::-1]))
        y0 = int(np.argmax(rows)); y1 = int(len(rows) - np.argmax(rows[::-1]))
        return x0, y0, x1, y1

    @property
    def source_type(self):
        return self._cap.source_type()

    @property
    def is_window_scoped(self):
        """True when captures are window-relative. Reads the portal's own
        source_type — a maximized window is screen-sized, so size cannot tell."""
        return self.source_type == SOURCE_WINDOW

    def is_calibrated(self):
        """True once the operator has picked this agent's window at least once."""
        return self._cap.token_file.exists()

    def forget(self):
        """Drop the cached pick so the next use prompts again."""
        try:
            self._cap.token_file.unlink()
        except FileNotFoundError:
            pass
        self._origin = None

    # ── locating the window on screen ────────────────────────────────────────

    @staticmethod
    def _decode(png):
        import io

        import cv2
        import numpy as np
        from PIL import Image
        arr = np.array(Image.open(io.BytesIO(png)).convert("RGB"))
        return cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY)

    @classmethod
    def _pick_patches(cls, win_gray, limit=6):
        """Candidate PATCH-sized tiles to search the screen for, best first.

        Two competing requirements:

        * The tile must be TEXTURED. A flat tile correlates ~1.0 against any
          flat screen region — the same failure that made four blank button
          templates match empty desktop.
        * The tile must be STABLE. An agent window is a live chat view whose
          text changes between the window capture and the screen capture, and a
          patch taken from that text then matches nothing. Measured: correlation
          collapsed from 1.000 to 0.40 on a VS Code window with streaming text.

        Window CHROME — toolbars, tab strips, sidebars, borders — is both
        textured and static, so tiles from the window's edges are preferred and
        interior (content) tiles are used only as fallbacks. Several are
        returned because any single one may be occluded by another window.
        """
        h, w = win_gray.shape
        if h < PATCH or w < PATCH:
            return [(0, 0, win_gray)]

        step = max(PATCH // 2, 1)
        edge = PATCH                     # how deep a band counts as "chrome"
        scored = []
        for y in range(0, h - PATCH + 1, step):
            for x in range(0, w - PATCH + 1, step):
                tile = win_gray[y:y + PATCH, x:x + PATCH]
                var = float(tile.var())
                if var < 25.0:           # too flat to be distinctive
                    continue
                # Distance from the nearest window edge; small = chrome.
                d = min(x, y, w - PATCH - x, h - PATCH - y)
                stable = 1.0 if d <= edge else 0.35
                scored.append((var * stable, x, y, tile))
        if not scored:
            return [(0, 0, win_gray[:PATCH, :PATCH])]
        scored.sort(key=lambda t: -t[0])
        return [(x, y, tile) for _, x, y, tile in scored[:limit]]

    def locate(self, screen_png=None, force=False):
        """Find this window's top-left corner in SCREEN coordinates.

        Returns ((x, y), confidence) or (None, confidence). Cached; pass
        force=True after the window may have moved.
        """
        if self._origin is not None and not force:
            return self._origin, self._origin_conf
        import cv2
        # Grab the screen FIRST and the window immediately after: the smaller
        # the gap, the less a live-updating window can change between them.
        screen = self._decode(screen_session().grab_png())
        win_png = self.capture_png()
        win = self._decode(win_png)
        bounds = self.content_bounds(win_png)
        if bounds:
            bx0, by0, bx1, by1 = bounds
            win = win[by0:by1, bx0:bx1]      # drop the black padding

        best = (None, 0.0)
        for px, py, patch in self._pick_patches(win):
            if patch.shape[0] > screen.shape[0] or patch.shape[1] > screen.shape[1]:
                continue
            res = cv2.matchTemplate(screen, patch, cv2.TM_CCOEFF_NORMED)
            _, conf, _, loc = cv2.minMaxLoc(res)
            if conf > best[1]:
                # The patch sits at (px, py) in the window, so the origin is the
                # patch's screen position minus that offset.
                best = ((loc[0] - px, loc[1] - py), float(conf))
            if conf >= 0.98:                 # unambiguous; stop searching
                break

        origin, conf = best
        self._origin_conf = conf
        if origin is None or conf < LOCATE_THRESH:
            self._origin = None
            return None, self._origin_conf
        self._origin = origin
        return self._origin, self._origin_conf

    def to_screen(self, x, y, force_locate=False):
        """Window-relative (x, y) -> absolute screen coordinates."""
        origin, conf = self.locate(force=force_locate)
        if origin is None:
            raise RuntimeError(
                f"could not locate {self.agent_id}'s window on screen "
                f"(best correlation {conf:.2f} < {LOCATE_THRESH}). The window "
                f"may be minimized, occluded, or on another workspace.")
        return origin[0] + int(x), origin[1] + int(y)

    # ── input, in WINDOW coordinates ─────────────────────────────────────────

    def move_to_window_xy(self, x, y, force_locate=False):
        sx, sy = self.to_screen(x, y, force_locate)
        screen_session().move_to(sx, sy)
        return sx, sy

    def click_at(self, x, y, button=BTN_LEFT, settle=0.12, retry_locate=True,
                 force_relocate=False):
        """Click a window-relative point.

        `force_relocate` re-finds the window before clicking; use it when the
        window is known to have moved. Otherwise the cached origin is used, and
        on failure it is discarded and the window re-located once — that is how
        a moved window is handled: re-see it, do not try to track it.
        """
        import time
        if force_relocate:
            self._origin = None
        try:
            sx, sy = self.to_screen(x, y, force_locate=force_relocate)
        except RuntimeError:
            if not retry_locate:
                raise
            self._origin = None
            sx, sy = self.to_screen(x, y, force_locate=True)
        s = screen_session()
        s.move_to(sx, sy)
        time.sleep(settle)
        s.button(button, True)
        time.sleep(0.02)
        s.button(button, False)
        return sx, sy


def agent_session(agent_id):
    """Process-wide session for `agent_id`, created on first use."""
    if agent_id not in AGENT_IDS:
        raise ValueError(f"unknown agent id {agent_id!r}; expected one of {AGENT_IDS}")
    if agent_id not in _sessions:
        _sessions[agent_id] = AgentWindowSession(agent_id)
    return _sessions[agent_id]


def calibrated_agents():
    """Agent ids whose window has already been picked (no prompt needed)."""
    return tuple(a for a in AGENT_IDS if AgentWindowSession(a).is_calibrated())


def close_all():
    for s in _sessions.values():
        try:
            s._cap.close()
        except Exception:
            pass
    _sessions.clear()
    global _screen
    if _screen is not None:
        try:
            _screen.close()
        except Exception:
            pass
        _screen = None
