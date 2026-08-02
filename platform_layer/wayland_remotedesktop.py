"""xdg-desktop-portal RemoteDesktop — pointer and keyboard injection on Wayland.

WHY NOT uinput
--------------
The first implementation here wrote to /dev/uinput with ABS_X/ABS_Y absolute
axes. The kernel accepted every write and raised nothing, but the pointer never
moved: `/proc/bus/input/devices` showed the device registered as

    N: Name="SOC Ultralight Virtual Pointer"
    H: Handlers=mouse2 event17 js0        <- joystick
    B: REL=100                            <- REL_WHEEL only, no REL_X/REL_Y
    B: ABS=3

An absolute-axis device carrying buttons but no relative axes gets claimed by
the joystick driver, and libinput will not drive the pointer from it. That is a
silent no-op of exactly the kind already found (and fixed) in platform_x11's
move_window — which is why every injection path here is verified by observing
the screen, never by "the call returned without raising".

uinput CAN be made to work with REL_X/REL_Y plus a slam-to-origin trick, but
relative motion is subject to libinput's pointer acceleration, so a requested
(x, y) is not where the cursor lands. SOC clicks exact coordinates inside agent
windows, so that is disqualifying.

RemoteDesktop's NotifyPointerMotionAbsolute takes coordinates in the
compositor's own space, tied to a ScreenCast stream. Exact, sanctioned, and no
device-permission games.

SESSION SHAPE
-------------
Absolute motion needs a stream to be absolute *against*, so the portal requires
one session that is BOTH a RemoteDesktop session and a ScreenCast session:

    RemoteDesktop.CreateSession
    RemoteDesktop.SelectDevices   (POINTER | KEYBOARD)
    ScreenCast.SelectSources      <- on the SAME session handle
    RemoteDesktop.Start           -> prompt once; returns streams + restore_token

The prompt for this is stronger than plain screen sharing — it grants control of
the pointer and keyboard. `persist_mode=2` means it is asked once and cached.
"""

from .wayland_portal import PERSIST_UNTIL_REVOKED, PortalSession

REMOTE_DESKTOP = "org.freedesktop.portal.RemoteDesktop"
SCREENCAST = "org.freedesktop.portal.ScreenCast"

DEVICE_KEYBOARD, DEVICE_POINTER = 1, 2
SOURCE_MONITOR = 1
CURSOR_EMBEDDED = 2

# evdev button codes, which is what the portal expects.
BTN_LEFT, BTN_RIGHT, BTN_MIDDLE = 0x110, 0x111, 0x112
AXIS_VERTICAL, AXIS_HORIZONTAL = 0, 1

STATE_RELEASED, STATE_PRESSED = 0, 1


class RemoteDesktopSession(PortalSession):
    """Pointer/keyboard injection against the real compositor."""

    IFACE = REMOTE_DESKTOP
    TOKEN_NAME = "remotedesktop_restore_token"

    def __init__(self):
        super().__init__()
        self._stream = None      # PipeWire node id, the absolute-coord frame
        self._size = None
        self._started = False
        self._fd = None          # PipeWire remote fd, for same-session capture
        self._pipeline = None
        self._sink = None

    # ── handshake ────────────────────────────────────────────────────────────

    def start(self):
        """Create + start the combined session. Prompts once, then silent."""
        if self._started:
            return
        from gi.repository import GLib
        self._connect()

        tok = self._token("req")
        self._session = self._request("CreateSession", GLib.Variant("(a{sv})", ({
            "handle_token": GLib.Variant("s", tok),
            "session_handle_token": GLib.Variant("s", self._token("sess")),
        },)), tok)["session_handle"]

        restore = self._load_token()
        tok = self._token("req")
        opts = {
            "handle_token": GLib.Variant("s", tok),
            "types": GLib.Variant("u", DEVICE_POINTER | DEVICE_KEYBOARD),
            "persist_mode": GLib.Variant("u", PERSIST_UNTIL_REVOKED),
        }
        if restore:
            opts["restore_token"] = GLib.Variant("s", restore)
        self._request("SelectDevices",
                      GLib.Variant("(oa{sv})", (self._session, opts)), tok)

        # Absolute motion is expressed against a stream, so this session must
        # also be a ScreenCast session — same session handle, ScreenCast iface.
        tok = self._token("req")
        self._request("SelectSources", GLib.Variant("(oa{sv})", (self._session, {
            "handle_token": GLib.Variant("s", tok),
            "types": GLib.Variant("u", SOURCE_MONITOR),
            "multiple": GLib.Variant("b", False),
            "cursor_mode": GLib.Variant("u", CURSOR_EMBEDDED),
        })), tok, iface=SCREENCAST)

        tok = self._token("req")
        results = self._request("Start", GLib.Variant("(osa{sv})", (
            self._session, "", {"handle_token": GLib.Variant("s", tok)})), tok)

        self._save_token(results.get("restore_token"))

        streams = results.get("streams") or []
        if not streams:
            raise RuntimeError(
                "RemoteDesktop.Start returned no streams — absolute pointer "
                "motion needs one. Was ScreenCast.SelectSources accepted?")
        self._stream, props = streams[0]
        self._size = tuple(props["size"]) if "size" in props else None
        self._started = True

    @property
    def stream_size(self):
        self.start()
        return self._size

    # ── capture on THIS session's stream ─────────────────────────────────────
    # Injection must be verifiable by observing the screen, and that only works
    # if the frames come from the same session (hence the same cursor mode) as
    # the pointer being moved. Capturing from a separate ScreenCast session
    # leaves cursor rendering as an uncontrolled variable.

    def _open_pipewire(self):
        if self._fd is not None:
            return self._fd
        from gi.repository import Gio, GLib
        self.start()
        reply, fds = self._bus.call_with_unix_fd_list_sync(
            "org.freedesktop.portal.Desktop", "/org/freedesktop/portal/desktop",
            SCREENCAST, "OpenPipeWireRemote",
            GLib.Variant("(oa{sv})", (self._session, {})),
            GLib.VariantType("(h)"), Gio.DBusCallFlags.NONE, -1, None, None)
        self._fd = fds.get(reply.unpack()[0])
        return self._fd

    def grab_png(self, warmup=5):
        """One PNG frame from this session's stream, cursor included."""
        import gi
        gi.require_version("Gst", "1.0")
        from gi.repository import Gst
        fd = self._open_pipewire()
        if self._pipeline is None:
            Gst.init(None)
            self._pipeline = Gst.parse_launch(
                f"pipewiresrc fd={fd} path={self._stream} ! videoconvert ! "
                f"pngenc ! appsink name=sink max-buffers=2 drop=true sync=false")
            self._sink = self._pipeline.get_by_name("sink")
            self._pipeline.set_state(Gst.State.PLAYING)
            for _ in range(warmup):
                self._sink.emit("try-pull-sample", 5 * Gst.SECOND)
        sample = self._sink.emit("try-pull-sample", 5 * Gst.SECOND)
        if sample is None:
            raise RuntimeError("no frame from the RemoteDesktop stream")
        buf = sample.get_buffer()
        ok, info = buf.map(Gst.MapFlags.READ)
        if not ok:
            raise RuntimeError("could not map frame buffer")
        try:
            return bytes(info.data)
        finally:
            buf.unmap(info)

    # ── injection ────────────────────────────────────────────────────────────

    def _notify(self, method, variant):
        self.start()
        self._call(method, variant)

    def move_to(self, x, y):
        """Absolute pointer motion, in compositor coordinates."""
        from gi.repository import GLib
        self.start()
        self._notify("NotifyPointerMotionAbsolute", GLib.Variant(
            "(oa{sv}udd)", (self._session, {}, self._stream, float(x), float(y))))

    def button(self, code=BTN_LEFT, pressed=True):
        from gi.repository import GLib
        self._notify("NotifyPointerButton", GLib.Variant(
            "(oa{sv}iu)", (self._session, {}, int(code),
                           STATE_PRESSED if pressed else STATE_RELEASED)))

    def click(self, code=BTN_LEFT):
        import time
        self.button(code, True)
        time.sleep(0.02)
        self.button(code, False)

    def scroll(self, steps, axis=AXIS_VERTICAL):
        """Discrete wheel steps; positive scrolls down per the portal's sign."""
        from gi.repository import GLib
        self._notify("NotifyPointerAxisDiscrete", GLib.Variant(
            "(oa{sv}ui)", (self._session, {}, int(axis), int(steps))))

    def keysym(self, sym, pressed=True):
        """Press/release an X11 keysym (portal takes keysyms directly)."""
        from gi.repository import GLib
        self._notify("NotifyKeyboardKeysym", GLib.Variant(
            "(oa{sv}iu)", (self._session, {}, int(sym),
                           STATE_PRESSED if pressed else STATE_RELEASED)))

    def type_keysym(self, sym):
        import time
        self.keysym(sym, True)
        time.sleep(0.01)
        self.keysym(sym, False)

    def close(self):
        if self._session and self._bus:
            try:
                self._call("Close", None,
                           iface="org.freedesktop.portal.Session")
            except Exception:
                pass
        self._started = False
        self._session = None
