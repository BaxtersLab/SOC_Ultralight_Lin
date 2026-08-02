"""Shared xdg-desktop-portal request plumbing.

Portal methods are asynchronous: calling one returns a Request object path, and
the real answer arrives later as a Response signal on that path. Everything that
talks to a portal needs the same handshake, so it lives here once.
"""

import os
import random
from pathlib import Path

PORTAL_NAME = "org.freedesktop.portal.Desktop"
PORTAL_OBJ = "/org/freedesktop/portal/desktop"
REQUEST_IFACE = "org.freedesktop.portal.Request"

PERSIST_UNTIL_REVOKED = 2

CACHE_DIR = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "soc-ultralight"


class PortalSession:
    """Base for a portal session: bus, request/response, token cache."""

    #: Subclasses set the interface they drive, e.g. "...portal.ScreenCast".
    IFACE = None
    #: Filename under CACHE_DIR holding this session type's restore token.
    TOKEN_NAME = None

    def __init__(self):
        self._bus = None
        self._sender = None
        self._loop = None
        self._session = None

    # ── bus ──────────────────────────────────────────────────────────────────

    def _connect(self):
        if self._bus is not None:
            return
        from gi.repository import Gio, GLib
        self._bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        # Request paths embed the caller's unique name, ':' stripped, '.' -> '_'.
        self._sender = self._bus.get_unique_name()[1:].replace(".", "_")
        self._loop = GLib.MainLoop()

    @staticmethod
    def _token(prefix):
        return f"{prefix}{random.randint(0, 2**31)}"

    def _request(self, method, variant, token, iface=None):
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
            self._bus.call_sync(PORTAL_NAME, PORTAL_OBJ, iface or self.IFACE,
                                method, variant, None, Gio.DBusCallFlags.NONE,
                                -1, None)
            self._loop.run()
        finally:
            self._bus.signal_unsubscribe(sub)

        code = out.get("code")
        if code != 0:
            raise RuntimeError(
                f"{(iface or self.IFACE).rsplit('.', 1)[-1]}.{method}: response "
                f"code {code} ({'cancelled by user' if code == 1 else 'ended'})")
        return out["results"]

    def _call(self, method, variant, iface=None):
        """Call a portal method that replies directly (no Request object)."""
        from gi.repository import Gio
        return self._bus.call_sync(
            PORTAL_NAME, PORTAL_OBJ, iface or self.IFACE, method, variant,
            None, Gio.DBusCallFlags.NONE, -1, None)

    # ── restore tokens ───────────────────────────────────────────────────────

    @property
    def _token_file(self):
        return CACHE_DIR / self.TOKEN_NAME

    def _load_token(self):
        try:
            return self._token_file.read_text().strip() or None
        except OSError:
            return None

    def _save_token(self, token):
        if not token:
            return
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        self._token_file.write_text(token)
        self._token_file.chmod(0o600)
