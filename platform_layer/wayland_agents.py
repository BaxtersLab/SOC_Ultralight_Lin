"""Per-agent window tracking on Wayland — SOC's replacement for window handles.

THE PROBLEM
-----------
SOC's per-agent config on Windows/X11 is:

    window handle + input-field XY + send-button XY   (absolute screen coords)

Wayland has no cross-client window API, so there is no handle to store
(`org.gnome.Shell.Introspect.GetWindows` returns "Access denied"), and absolute
screen coordinates break the moment the operator moves a window.

TWO MEASURED CONSTRAINTS SHAPE THIS DESIGN
------------------------------------------
1. GNOME grants input devices only to whole-screen sessions:

       whole-screen : source_type=1 MONITOR, devices=3 KEYBOARD|POINTER
       per-window   : source_type=2 WINDOW,  devices NONE
                      -> "Session is not allowed to call NotifyPointer methods"

   A pointer is global to the compositor, so "inject into a window" has no
   meaning. Clicks must therefore go through a whole-screen session, in
   absolute screen coordinates.

2. Two concurrent PipeWire streams permanently freeze each other:

       screen stream alone                10/10 distinct frames over 10 s
       screen + a window stream            1/8  distinct frames   FROZEN
       window pipeline then torn down      1/6  distinct frames   STILL frozen

   The freeze is at the PipeWire connection level and does not recover, so
   exactly ONE stream may be open at a time.

THE DESIGN
----------
One persistent stream, and a visual reference per agent:

    calibration (once per agent, interactive)
        close the desktop stream
        open a WINDOW portal session, capture the agent's window
        save the cropped window content as a reference image on disk
        close the window session
        reopen the desktop stream

    runtime (no prompts, one stream)
        locate()  correlate the reference image into a desktop frame
                  -> the window's current on-screen origin
        image()   crop that region out of the desktop frame
                  -> window-relative pixels for OCR / template matching
        click_at() origin + window-relative XY -> absolute click

The reference persists on disk, so calibration survives restarts and the
window session is never needed again. Moving the window changes only the
located origin, so **calibration survives the operator dragging a window** —
which absolute screen coordinates cannot do on any platform.

    a = agent_window("agent1")
    if not a.is_calibrated():
        a.calibrate()               # one portal prompt, once, ever
    a.click_at(120, 480)            # window-relative; follows the window
    png = a.image()                 # just that window, for OCR
"""

import io
import os
from pathlib import Path

from .wayland_remotedesktop import (BTN_LEFT, RemoteDesktopSession,
                                    SOURCE_MONITOR)
from .wayland_screencast import CURSOR_HIDDEN, SOURCE_WINDOW, ScreenCastSession

#: Agent ids SOC calibrates. agent4 (A4v, vision) is deliberately absent — it is
#: not driven by click targets, and soc_ultralight's _apply_template_match
#: likewise only handles agent1/2/3/5.
AGENT_IDS = ("agent1", "agent2", "agent3", "agent5")

CACHE_DIR = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "soc-ultralight"

#: Minimum correlation for a locate to be trusted. Below this the window is
#: reported missing rather than guessed at — a wrong origin means clicking the
#: wrong place, which is worse than not clicking.
LOCATE_THRESH = 0.75
#: Side of the reference patch correlated against the desktop frame.
PATCH = 160

_windows = {}
_desktop = None


# ── the single persistent stream ─────────────────────────────────────────────

class WaylandDesktop:
    """The one stream: whole-screen capture plus pointer/keyboard injection.

    Exactly one PipeWire stream may exist at a time (see module docstring), and
    this is it. `suspended()` releases it so calibration can briefly open a
    window session, and restores it afterwards.
    """

    def __init__(self):
        self._session = RemoteDesktopSession()

    # capture ---------------------------------------------------------------

    def frame(self, fresh=False):
        """Current desktop as PNG bytes.

        `fresh=True` discards any queued backlog and waits for a frame
        captured AFTER the call. Use it whenever something has just changed
        the screen — a window move, say — because queued buffers may predate
        the change and would otherwise be measured as though current.
        """
        return self._session.grab_png(fresh=fresh)

    @property
    def size(self):
        return self._session.stream_size

    # input -----------------------------------------------------------------

    def move_to(self, x, y):
        self._session.move_to(x, y)

    def click(self, x, y, button=BTN_LEFT, settle=0.12):
        import time
        self._session.move_to(x, y)
        time.sleep(settle)          # let the motion be delivered before the press
        self._session.button(button, True)
        time.sleep(0.02)
        self._session.button(button, False)

    def scroll(self, steps):
        self._session.scroll(steps)

    def key(self, keysym):
        self._session.type_keysym(keysym)

    # stream lifecycle ------------------------------------------------------

    def release(self):
        """Drop the stream so another may be opened. Idempotent."""
        try:
            self._session.close()
        except Exception:
            pass
        self._session = RemoteDesktopSession()

    class _Suspended:
        def __init__(self, desktop):
            self._d = desktop

        def __enter__(self):
            self._d.release()
            return self._d

        def __exit__(self, *exc):
            self._d.frame()          # re-establish eagerly, so the caller is
            return False             # not surprised by a cold first grab

    def suspended(self):
        """Context manager releasing the stream for the duration of the block."""
        return self._Suspended(self)


def desktop():
    """Process-wide desktop stream."""
    global _desktop
    if _desktop is None:
        _desktop = WaylandDesktop()
    return _desktop


# ── per-agent window, tracked visually ───────────────────────────────────────

class AgentWindow:
    """One agent's window, identified by a saved reference image."""

    def __init__(self, agent_id):
        self.agent_id = agent_id
        self._origin = None
        self._origin_conf = 0.0
        self._ref = None            # lazily loaded greyscale reference
        self._ref_size = None

    # calibration -----------------------------------------------------------

    @property
    def reference_path(self):
        return CACHE_DIR / f"{self.agent_id}_reference.png"

    def is_calibrated(self):
        return self.reference_path.exists()

    def calibrate(self, keep=True):
        """Capture this agent's window once and save it as the reference.

        Opens a WINDOW portal session — the operator picks the window — while
        the desktop stream is released, because two streams cannot coexist.
        The window session is closed immediately afterwards and never needed
        again: everything at runtime works from the desktop stream plus this
        saved image.
        """
        cap = ScreenCastSession(
            source_types=SOURCE_WINDOW,
            token_name=f"{self.agent_id}_window_token",
            # Keep the pointer out of the reference: it feeds correlation and
            # template matching, and a cursor baked into the image would be
            # matched as though it were part of the window.
            cursor_mode=CURSOR_HIDDEN)
        with desktop().suspended():
            png = cap.grab_png()
            if cap.source_type() != SOURCE_WINDOW:
                cap.close()
                raise RuntimeError(
                    f"{self.agent_id}: the portal granted a MONITOR, not a "
                    f"window. Re-run and choose the Window tab.")
            content = self._content_bounds(png)
            if content is None:
                cap.close()
                raise RuntimeError(f"{self.agent_id}: captured frame was blank")
            image = self._crop_png(png, content)
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            self.reference_path.write_bytes(image)
            cap.close()
        self._ref = self._ref_size = None
        self._origin = None
        if not keep:
            self.forget()
        return self.reference_size

    def forget(self):
        """Drop the reference so the agent must be calibrated again."""
        for p in (self.reference_path,
                  CACHE_DIR / f"{self.agent_id}_window_token"):
            try:
                p.unlink()
            except FileNotFoundError:
                pass
        self._ref = self._ref_size = self._origin = None

    # reference -------------------------------------------------------------

    def _load_reference(self):
        if self._ref is not None:
            return self._ref
        if not self.is_calibrated():
            raise RuntimeError(
                f"{self.agent_id} is not calibrated — call calibrate() and pick "
                f"its window once.")
        self._ref = self._decode(self.reference_path.read_bytes())
        self._ref_size = (self._ref.shape[1], self._ref.shape[0])
        return self._ref

    @property
    def reference_size(self):
        """(w, h) of the window as calibrated."""
        self._load_reference()
        return self._ref_size

    # image helpers ---------------------------------------------------------

    @staticmethod
    def _decode(png):
        import cv2
        import numpy as np
        from PIL import Image
        arr = np.array(Image.open(io.BytesIO(png)).convert("RGB"))
        return cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY)

    @staticmethod
    def _content_bounds(png, threshold=8):
        """Non-padding region of a window capture.

        GNOME delivers a window stream in a MONITOR-sized buffer with the
        window drawn at (0,0) and the remainder black-padded, and reports only
        the buffer size. Measured: a 1200x800 window arrived in a 1920x1080
        frame. The true window size has to be measured from the content.
        """
        import numpy as np
        from PIL import Image
        a = np.array(Image.open(io.BytesIO(png)).convert("L"))
        mask = a > threshold
        cols, rows = mask.any(axis=0), mask.any(axis=1)
        if not cols.any() or not rows.any():
            return None
        x0 = int(np.argmax(cols)); x1 = int(len(cols) - np.argmax(cols[::-1]))
        y0 = int(np.argmax(rows)); y1 = int(len(rows) - np.argmax(rows[::-1]))
        return x0, y0, x1, y1

    @staticmethod
    def _crop_png(png, box):
        from PIL import Image
        im = Image.open(io.BytesIO(png)).convert("RGB").crop(box)
        out = io.BytesIO()
        im.save(out, format="PNG")
        return out.getvalue()

    @staticmethod
    def _patches(ref, limit=6):
        """Reference tiles to correlate, best first.

        Two competing requirements:

        * TEXTURED. A flat tile correlates ~1.0 against any flat screen region
          — the failure that made four blank button templates match empty
          desktop.
        * STABLE. An agent window is a live chat view. A patch taken from
          changing text matches nothing a moment later: correlation was
          measured collapsing from 1.000 to 0.40 on a window with streaming
          text. Window CHROME — toolbars, tab strips, sidebars — is both
          textured and static, so edge tiles are strongly preferred.

        Several are returned because any one may be occluded by another window.
        """
        h, w = ref.shape
        if h < PATCH or w < PATCH:
            return [(0, 0, ref)]
        step = max(PATCH // 2, 1)
        scored = []
        for y in range(0, h - PATCH + 1, step):
            for x in range(0, w - PATCH + 1, step):
                tile = ref[y:y + PATCH, x:x + PATCH]
                var = float(tile.var())
                if var < 25.0:
                    continue
                near_edge = min(x, y, w - PATCH - x, h - PATCH - y) <= PATCH
                scored.append((var * (1.0 if near_edge else 0.35), x, y, tile))
        if not scored:
            return [(0, 0, ref[:PATCH, :PATCH])]
        scored.sort(key=lambda t: -t[0])
        return [(x, y, tile) for _, x, y, tile in scored[:limit]]

    # locating --------------------------------------------------------------

    def _measure(self, frame_png=None):
        """One correlation pass: reference -> desktop frame. ((x,y), conf)."""
        import cv2
        ref = self._load_reference()
        # A re-measure must use a frame captured AFTER this call. A queued
        # pre-move buffer otherwise reports the window's OLD origin at full
        # confidence, and the click that follows lands where the window used
        # to be.
        screen = self._decode(frame_png or desktop().frame(fresh=True))
        best = (None, 0.0)
        for px, py, patch in self._patches(ref):
            if patch.shape[0] > screen.shape[0] or patch.shape[1] > screen.shape[1]:
                continue
            res = cv2.matchTemplate(screen, patch, cv2.TM_CCOEFF_NORMED)
            _, conf, _, loc = cv2.minMaxLoc(res)
            if conf > best[1]:
                # The patch sits at (px, py) in the reference, so the window
                # origin is its screen position minus that offset.
                best = ((loc[0] - px, loc[1] - py), float(conf))
            if conf >= 0.98:
                break
        return best

    def locate(self, frame_png=None, force=False, confirm=True):
        """Find the window's current top-left on screen.

        Returns ((x, y), confidence), or (None, confidence) when the window
        cannot be found — minimized, occluded, or on another workspace.

        A re-measure is CONFIRMED by a second, later measurement that must
        agree. One sample of a changing scene can be stale even when it is
        "fresh": the compositor repaint and the PipeWire delivery both lag the
        event that moved the window, so a single pass taken moments after a
        move reports the OLD origin at confidence 1.000. Two passes separated
        in time cannot both be stale, and the later one wins when they differ.
        Only relevant when re-measuring, so the cached-origin fast path is
        unaffected.
        """
        if self._origin is not None and not force:
            return self._origin, self._origin_conf

        origin, conf = self._measure(frame_png)
        if confirm and frame_png is None and origin is not None:
            again, conf2 = self._measure()
            if again is not None and again != origin:
                origin, conf = again, conf2

        self._origin_conf = conf
        self._origin = origin if (origin is not None and conf >= LOCATE_THRESH) else None
        return self._origin, self._origin_conf

    def to_screen(self, x, y, force_locate=False):
        """Window-relative (x, y) -> absolute screen coordinates."""
        origin, conf = self.locate(force=force_locate)
        if origin is None:
            raise WindowNotFound(
                f"could not locate {self.agent_id}'s window (best correlation "
                f"{conf:.2f} < {LOCATE_THRESH}); it may be minimized, occluded, "
                f"or on another workspace")
        return origin[0] + int(x), origin[1] + int(y)

    # runtime ---------------------------------------------------------------

    def image(self, frame_png=None):
        """This window's pixels, cropped out of the desktop frame.

        Window-relative, so OCR and template matching work in the same
        coordinates as click_at regardless of where the window has been moved.
        """
        frame = frame_png or desktop().frame(fresh=True)
        origin, _ = self.locate(frame_png=frame, force=True)
        if origin is None:
            raise WindowNotFound(f"{self.agent_id}'s window is not visible")
        w, h = self.reference_size
        return self._crop_png(frame, (origin[0], origin[1],
                                      origin[0] + w, origin[1] + h))

    def click_at(self, x, y, button=BTN_LEFT, retry_locate=True):
        """Click a window-relative point.

        A cached origin is used when available; on failure it is discarded and
        the window re-located once. That is how a moved window is handled —
        re-see it, rather than trying to track it.
        """
        try:
            sx, sy = self.to_screen(x, y)
        except WindowNotFound:
            if not retry_locate:
                raise
            self._origin = None
            sx, sy = self.to_screen(x, y, force_locate=True)
        desktop().click(sx, sy, button)
        return sx, sy


class WindowNotFound(RuntimeError):
    """The agent's window could not be found on screen."""


def agent_window(agent_id):
    """Process-wide AgentWindow for `agent_id`."""
    if agent_id not in AGENT_IDS:
        raise ValueError(f"unknown agent id {agent_id!r}; expected one of {AGENT_IDS}")
    if agent_id not in _windows:
        _windows[agent_id] = AgentWindow(agent_id)
    return _windows[agent_id]


def calibrated_agents():
    """Agent ids that already have a saved reference image."""
    return tuple(a for a in AGENT_IDS if AgentWindow(a).is_calibrated())
