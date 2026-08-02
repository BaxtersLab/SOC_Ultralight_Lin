"""xdg-desktop-portal ScreenCast -> PipeWire capture for the Wayland backend.

This is SOC's replacement for the mss/X11 screen grab in pipeline A. It is the
same mechanism OBS uses on Wayland (obs-pipewire is a portal client), and it was
proven end to end on Ubuntu 26.04 / GNOME 50 before this module was written.

The session is held OPEN across grabs: the handshake costs ~94 ms and a frame
costs ~50 ms, so re-handshaking per poll would be absurd.

The first Start() shows the GNOME picker once. `persist_mode=2` makes the portal
return a `restore_token`, which is cached so every later run reconnects silently.
"""

import hashlib
import os
import random
from pathlib import Path

PORTAL_NAME = "org.freedesktop.portal.Desktop"
PORTAL_OBJ = "/org/freedesktop/portal/desktop"
SCREENCAST = "org.freedesktop.portal.ScreenCast"
REQUEST_IFACE = "org.freedesktop.portal.Request"

SOURCE_MONITOR, SOURCE_WINDOW = 1, 2
CURSOR_HIDDEN, CURSOR_EMBEDDED = 1, 2
PERSIST_UNTIL_REVOKED = 2

CACHE_DIR = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "soc-ultralight"
TOKEN_FILE = CACHE_DIR / "screencast_restore_token"

# Early buffers arrive blank while the stream negotiates format.
WARMUP_FRAMES = 5
# Upper bound when draining a queued backlog to reach the newest frame.
MAX_DRAIN = 30


class ScreenCastSession:
    """One portal ScreenCast session plus its GStreamer pipeline."""

    def __init__(self, source_types=SOURCE_MONITOR | SOURCE_WINDOW,
                 token_name=None, cursor_mode=CURSOR_EMBEDDED):
        """token_name: file under CACHE_DIR holding this session's restore
        token. A token encodes WHAT THE OPERATOR PICKED, so a session asking for
        a different source type must use a different token — otherwise the
        portal silently restores the old grant and ignores `source_types`."""
        self._source_types = source_types
        self._token_name = token_name or "screencast_restore_token"
        self._cursor_mode = cursor_mode
        self._bus = None
        self._sender = None
        self._loop = None
        self._session = None
        self._node_id = None
        self._fd = None
        self._pipeline = None
        self._sink = None
        self._size = None
        self._source_type = None
        self._last_frame = None   # see grab_png: streams are damage-driven
        self._last_hash = None

    # ── portal plumbing ──────────────────────────────────────────────────────

    def _connect(self):
        if self._bus is not None:
            return
        from gi.repository import Gio, GLib
        self._bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        # Request paths embed the caller's unique name, ':' stripped, '.'->'_'.
        self._sender = self._bus.get_unique_name()[1:].replace(".", "_")
        self._loop = GLib.MainLoop()

    @staticmethod
    def _token(prefix):
        return f"{prefix}{random.randint(0, 2**31)}"

    def _request(self, method, variant, token):
        """Call a portal method and block until its async Response arrives."""
        from gi.repository import Gio
        req_path = f"{PORTAL_OBJ}/request/{self._sender}/{token}"
        out = {}

        def on_response(_c, _s, _p, _i, _sig, params):
            out["code"], out["results"] = params.unpack()
            self._loop.quit()

        # Subscribe BEFORE calling — otherwise a fast reply races us.
        sub = self._bus.signal_subscribe(
            PORTAL_NAME, REQUEST_IFACE, "Response", req_path, None,
            Gio.DBusSignalFlags.NONE, on_response)
        try:
            self._bus.call_sync(PORTAL_NAME, PORTAL_OBJ, SCREENCAST, method,
                                variant, None, Gio.DBusCallFlags.NONE, -1, None)
            self._loop.run()
        finally:
            self._bus.signal_unsubscribe(sub)

        code = out.get("code")
        if code != 0:
            raise RuntimeError(
                f"ScreenCast.{method}: response code {code} "
                f"({'cancelled by user' if code == 1 else 'ended'})")
        return out["results"]

    def start(self):
        """Run the handshake. Prompts only if no valid restore token is cached."""
        if self._session is not None:
            return
        from gi.repository import Gio, GLib
        self._connect()

        tok = self._token("req")
        self._session = self._request("CreateSession", GLib.Variant("(a{sv})", ({
            "handle_token": GLib.Variant("s", tok),
            "session_handle_token": GLib.Variant("s", self._token("sess")),
        },)), tok)["session_handle"]

        restore = None
        tf = self.token_file
        if tf.exists():
            restore = tf.read_text().strip() or None

        tok = self._token("req")
        opts = {
            "handle_token": GLib.Variant("s", tok),
            "types": GLib.Variant("u", self._source_types),
            "multiple": GLib.Variant("b", False),
            "cursor_mode": GLib.Variant("u", self._cursor_mode),
            "persist_mode": GLib.Variant("u", PERSIST_UNTIL_REVOKED),
        }
        if restore:
            opts["restore_token"] = GLib.Variant("s", restore)
        self._request("SelectSources",
                      GLib.Variant("(oa{sv})", (self._session, opts)), tok)

        tok = self._token("req")
        results = self._request("Start", GLib.Variant("(osa{sv})", (
            self._session, "", {"handle_token": GLib.Variant("s", tok)})), tok)

        new_token = results.get("restore_token")
        if new_token:
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            tf = self.token_file
            tf.write_text(new_token)
            tf.chmod(0o600)

        streams = results.get("streams") or []
        if not streams:
            raise RuntimeError("ScreenCast returned no streams")
        self._node_id, props = streams[0]
        self._size = tuple(props["size"]) if "size" in props else None
        # Authoritative: 1=MONITOR, 2=WINDOW, 4=VIRTUAL. Do not infer from size.
        self._source_type = props.get("source_type")

        reply, fds = self._bus.call_with_unix_fd_list_sync(
            PORTAL_NAME, PORTAL_OBJ, SCREENCAST, "OpenPipeWireRemote",
            GLib.Variant("(oa{sv})", (self._session, {})),
            GLib.VariantType("(h)"), Gio.DBusCallFlags.NONE, -1, None, None)
        self._fd = fds.get(reply.unpack()[0])

    @property
    def token_file(self):
        return CACHE_DIR / self._token_name

    def source_type(self):
        """1=MONITOR, 2=WINDOW, 4=VIRTUAL — what the portal granted."""
        self.start()
        return self._source_type

    def stream_size(self):
        """(w, h) of the captured source. Kept a METHOD for compatibility;
        RemoteDesktopSession exposes the same thing as a property."""
        self.start()
        return self._size

    # ── GStreamer pipeline ───────────────────────────────────────────────────

    def _ensure_pipeline(self):
        if self._pipeline is not None:
            return
        import gi
        gi.require_version("Gst", "1.0")
        from gi.repository import Gst
        self.start()
        Gst.init(None)
        # pngenc rather than a raw appsink: each buffer is then a complete PNG,
        # so there is no stride/padding arithmetic to get wrong.
        self._pipeline = Gst.parse_launch(
            f"pipewiresrc fd={self._fd} path={self._node_id} ! videoconvert ! "
            f"pngenc ! appsink name=sink max-buffers=4 drop=true sync=false")
        self._sink = self._pipeline.get_by_name("sink")
        self._pipeline.set_state(Gst.State.PLAYING)
        # KEEP the warmup frames. These streams are damage-driven: a static
        # source may deliver only a handful of buffers ever, so discarding the
        # warmup can consume every frame the stream will produce and leave the
        # next pull blocking forever. Early buffers can be blank while format
        # negotiates, so later ones overwrite earlier ones.
        for _ in range(WARMUP_FRAMES):
            frame = self._pull(timeout_s=5)
            if frame:
                self._last_frame = frame

    def _pull(self, timeout_s=5):
        from gi.repository import Gst
        sample = self._sink.emit("try-pull-sample", timeout_s * Gst.SECOND)
        if sample is None:
            return None
        buf = sample.get_buffer()
        ok, info = buf.map(Gst.MapFlags.READ)
        if not ok:
            return None
        try:
            return bytes(info.data)
        finally:
            buf.unmap(info)

    def grab_png(self, path=None):
        """One frame as PNG bytes; optionally written to `path`.

        PipeWire screencast streams are DAMAGE-DRIVEN: a window that has not
        repainted produces no new buffers, and try-pull-sample simply times
        out. That is not an error — the window still looks exactly like its
        last frame — so the most recent frame is retained and returned. Only a
        stream that has never produced a frame raises.
        """
        self._ensure_pipeline()
        # appsink is drop=true ON PURPOSE. With drop=false the queue fills,
        # appsink back-pressures, and PipeWire STOPS PRODUCING — the stream
        # delivers one burst and then freezes forever. Measured: two screen
        # captures taken either side of a window move were 0.0% different.
        # drop=true keeps the producer running and always yields recent frames.
        #
        # Drain any small backlog so we act on the NEWEST frame, then fall back
        # to the retained one: these streams are damage-driven, so a static
        # source legitimately produces nothing new and its last frame still
        # describes it correctly.
        data = None
        for _ in range(MAX_DRAIN):
            frame = self._pull(timeout_s=0.15)
            if frame is None:
                break
            data = frame
        if data is None:
            data = self._last_frame
        else:
            self._last_frame = data
        if data is None:
            raise RuntimeError(
                "no frame has ever arrived from the PipeWire node — the source "
                "may be minimized, on another workspace, or the session was "
                "revoked")
        if path:
            Path(path).write_bytes(data)
        return data

    def changed(self):
        """Has the screen changed since the last call? ~13 ms, no OCR."""
        data = self.grab_png()
        digest = hashlib.blake2b(data, digest_size=16).digest()
        changed = digest != self._last_hash
        self._last_hash = digest
        return changed

    def close(self):
        if self._pipeline is not None:
            from gi.repository import Gst
            self._pipeline.set_state(Gst.State.NULL)
            self._pipeline = self._sink = None
        if self._fd is not None:
            try:
                os.close(self._fd)
            except OSError:
                pass
            self._fd = None
        self._session = None
