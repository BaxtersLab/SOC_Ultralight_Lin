"""Wayland backend (S15b) — SOC's platform seam on Linux/Wayland.

WHY THIS EXISTS
---------------
Ubuntu 26.04 / GNOME 50 cannot run an Xorg session at all: `gnome-shell --help`
offers only --wayland / --no-x11 / --headless, and `gnome-session-xsession` does
not exist in the archive. The X11 backend still works through XWayland, but an
XWayland client can only see other XWayland clients — never native Wayland
windows or the real desktop. So it cannot drive the operator's actual session.

This backend uses the sanctioned Wayland interfaces instead:
  screen capture  — xdg-desktop-portal ScreenCast -> PipeWire -> GStreamer
  input injection — /dev/uinput (a kernel virtual device, below the display
                    server, so it works on X11 and Wayland alike)
  screen geometry — org.gnome.Shell.Introspect ScreenSize (a D-Bus property)

DECLARED LIMITATIONS (Article VII §2 — these are NOT silent stubs)
------------------------------------------------------------------
Wayland has **no cross-client window API, by design**. A client cannot list,
locate, focus, move, or measure another application's windows. This was verified
on this machine, not assumed: `org.gnome.Shell.Introspect.GetWindows` exists but
returns **"Access denied"** to ordinary callers (GNOME allowlists it), and there
is no protocol for querying the global pointer position.

So these SIX contract methods have no honest implementation here and report
their documented "nothing" value while logging once:

    find_windows()      -> []       window_from_point() -> None
    get_window_rect()   -> None     is_window()         -> False
    move_window()       -> False    focus_window()      -> False
    cursor_pos()        -> (0, 0)   [see UNSUPPORTED below]

Callers must check `UNSUPPORTED` / `supports()` rather than assume. The porting
answer is NOT to fake these: replace the "window handle + input-field XY" model
with a per-agent portal restore token plus coordinates inside that window's
captured stream. See handoffs.md.

WHAT DOES WORK
--------------
    virtual_screen()          via the compositor's own D-Bus property
    set_cursor_pos()          via uinput absolute pointer
    click() / scroll()        via uinput (extensions, not in the contract)
    grab_screen()             via portal ScreenCast (extension — this is the
                              replacement for SOC's mss/X11 grab in pipeline A)
    acquire_instance_lock()   abstract-socket lock, identical to the X11 backend
    install_input_hook()      returns False, same fallback contract as X11
    left_button_down()        only with /dev/input read access; see below

PERMISSIONS
-----------
uinput injection needs write access to /dev/uinput, which is root-only by
default. `uinput_status()` reports exactly what is missing and how to fix it
(a udev rule — a *permissions* change, never display-server configuration).
"""

import ctypes
import fcntl
import os
import socket
import struct
import time

# ── uinput / evdev constants (linux/input-event-codes.h) ────────────────────
EV_SYN, EV_KEY, EV_REL, EV_ABS = 0x00, 0x01, 0x02, 0x03
SYN_REPORT = 0
REL_WHEEL = 0x08
ABS_X, ABS_Y = 0x00, 0x01
BTN_LEFT, BTN_RIGHT, BTN_MIDDLE = 0x110, 0x111, 0x112

UI_DEV_CREATE, UI_DEV_DESTROY = 0x5501, 0x5502
UI_SET_EVBIT = 0x40045564
UI_SET_KEYBIT = 0x40045565
UI_SET_RELBIT = 0x40045566
UI_SET_ABSBIT = 0x40045567

UINPUT_DEV = "/dev/uinput"
# uinput maps its absolute axes onto this fixed logical range; screen pixels are
# scaled into it, so the device does not need recreating when resolution changes.
ABS_MAX = 32767

_EVENT_FMT = "llHHi"          # struct input_event on 64-bit: timeval, type, code, value
_UINPUT_USER_DEV_FMT = "80sHHHHi" + "i" * (64 * 4)

SHELL_NAME = "org.gnome.Shell"
SHELL_PATH = "/org/gnome/Shell/Introspect"
SHELL_IFACE = "org.gnome.Shell.Introspect"


class WaylandPlatform:
    name = "wayland"

    #: Contract methods with no honest Wayland implementation. Callers should
    #: consult this (or supports()) instead of trusting a return value.
    UNSUPPORTED = frozenset({
        "find_windows", "window_from_point", "focus_window",
        "get_window_rect", "is_window", "move_window", "cursor_pos",
    })

    def __init__(self):
        self._lock_sock = None      # instance lock, held for process lifetime
        self._uinput_fd = None      # legacy/fallback virtual device, see below
        self._warned = set()        # one log line per unsupported method
        self._cursor = (0, 0)       # dead reckoning: where WE last put it
        self._screen_cache = None
        self._portal = None         # lazy ScreenCast session (capture)
        self._remote_session = None  # lazy RemoteDesktop session (injection)

    # ── Capability reporting ─────────────────────────────────────────────────

    def supports(self, method: str) -> bool:
        """False for the methods Wayland structurally cannot provide."""
        return method not in self.UNSUPPORTED

    def _unsupported(self, method, value):
        if method not in self._warned:
            self._warned.add(method)
            print(f"[wayland] {method}() is not available on Wayland — no "
                  f"cross-client window API exists. Returning {value!r}. "
                  f"See platform_wayland module docstring.")
        return value

    # ── Window operations: structurally unavailable ──────────────────────────

    def find_windows(self):
        return self._unsupported("find_windows", [])

    def window_from_point(self, x, y):
        return self._unsupported("window_from_point", None)

    def focus_window(self, handle) -> bool:
        return self._unsupported("focus_window", False)

    def get_window_rect(self, handle):
        return self._unsupported("get_window_rect", None)

    def is_window(self, handle) -> bool:
        return self._unsupported("is_window", False)

    def move_window(self, handle, x, y, w, h) -> bool:
        return self._unsupported("move_window", False)

    # ── Screen geometry ──────────────────────────────────────────────────────

    def virtual_screen(self):
        """(x, y, w, h) of the whole desktop, from the compositor itself.

        Uses the org.gnome.Shell.Introspect ScreenSize *property*, which is
        readable even though the GetWindows *method* on the same interface is
        access-denied. Deliberately avoids GTK/GDK: calling Gtk.init() merely to
        read geometry brings up a GL context, which crashes against NVIDIA
        595.58.03 on this box, and GDK would answer with XWayland's view anyway.
        """
        if self._screen_cache is not None:
            return self._screen_cache
        size = self._shell_screen_size() or self._portal_screen_size() or (1920, 1080)
        self._screen_cache = (0, 0, size[0], size[1])
        return self._screen_cache

    @staticmethod
    def _shell_screen_size():
        try:
            import gi
            from gi.repository import Gio, GLib
            bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
            reply = bus.call_sync(
                SHELL_NAME, SHELL_PATH, "org.freedesktop.DBus.Properties", "Get",
                GLib.Variant("(ss)", (SHELL_IFACE, "ScreenSize")),
                GLib.VariantType("(v)"), Gio.DBusCallFlags.NONE, 2000, None)
            w, h = reply.unpack()[0]
            return int(w), int(h)
        except Exception:
            return None

    def _portal_screen_size(self):
        try:
            return self._screencast().stream_size()
        except Exception:
            return None

    # ── Cursor ───────────────────────────────────────────────────────────────

    def cursor_pos(self):
        """UNSUPPORTED: Wayland has no protocol to query the global pointer.

        Returns the last position THIS process set via set_cursor_pos (dead
        reckoning), which is (0, 0) until we move it. It does NOT track the
        operator's physical mouse — nothing on Wayland can.
        """
        return self._unsupported("cursor_pos", self._cursor)

    def set_cursor_pos(self, x, y):
        """Move the pointer to an absolute screen position.

        Uses the RemoteDesktop portal, which expresses motion in the
        compositor's own coordinate space. uinput is NOT used for this: an
        absolute-axis uinput device gets claimed by the joystick driver and
        libinput will not drive the pointer from it (the writes succeed and
        nothing moves), while a relative-axis device is subject to pointer
        acceleration so the cursor does not land where asked. See
        wayland_remotedesktop for the full diagnosis.
        """
        x, y = int(x), int(y)
        self._remote().move_to(x, y)
        self._cursor = (x, y)

    def left_button_down(self) -> bool:
        """True while the physical left button is held.

        Needs read access to /dev/input/event* (the `input` group). Without it
        there is no way to observe physical buttons, and this reports False —
        which matches the X11 backend's behaviour when nothing is pressed, and
        is the safe answer for SOC's attribution logic (it only ever suppresses
        automation, never triggers it).
        """
        try:
            import evdev
        except ImportError:
            return False
        try:
            for path in evdev.list_devices():
                dev = evdev.InputDevice(path)
                try:
                    if BTN_LEFT in dev.active_keys():
                        return True
                finally:
                    dev.close()
        except Exception:
            return False
        return False

    # ── uinput injection ─────────────────────────────────────────────────────

    def uinput_status(self):
        """(ok, message) — whether injection is usable, and what to fix if not.

        Distinguishes the two failure modes, because they need opposite actions:
        the rule/group is missing (run the setup), versus the rule and group are
        already correct but THIS session predates the group change (log out).
        Reporting "rule not installed" in the second case sends people chasing
        something that is already done.
        """
        if not os.path.exists(UINPUT_DEV):
            return False, (f"{UINPUT_DEV} does not exist — the uinput module is "
                           f"not loaded (`sudo modprobe uinput`).")
        if os.access(UINPUT_DEV, os.W_OK):
            return True, f"{UINPUT_DEV} is writable — injection available."

        # Not writable. Is the user already a member of the owning group, with
        # only the login session out of date?
        try:
            import grp
            import pwd
            gid = os.stat(UINPUT_DEV).st_gid
            group = grp.getgrgid(gid)
            user = pwd.getpwuid(os.getuid()).pw_name
            enrolled = user in group.gr_mem or pwd.getpwuid(os.getuid()).pw_gid == gid
            active = gid in os.getgroups()
        except Exception:
            group, enrolled, active = None, False, False

        if group is not None and enrolled and not active:
            return False, (
                f"{UINPUT_DEV} is {group.gr_name}-owned and you ARE a member of "
                f"'{group.gr_name}' — but this login session started before that "
                f"was granted, so the process does not carry the group yet.\n"
                f"  Setup is already correct. Just LOG OUT AND BACK IN.\n"
                f"  (`sg`/`newgrp` are not shipped on Ubuntu 26.04, so there is "
                f"no way to pick the group up without a new session.)")

        return False, (
            f"{UINPUT_DEV} exists but is not writable by this user.\n"
            f"  Fix with a udev rule (a PERMISSIONS change — not display config):\n"
            f"    echo 'KERNEL==\"uinput\", GROUP=\"input\", MODE=\"0660\", "
            f"OPTIONS+=\"static_node=uinput\"' \\\n"
            f"      | sudo tee /etc/udev/rules.d/99-soc-uinput.rules\n"
            f"    sudo usermod -aG input $USER\n"
            f"    sudo udevadm control --reload-rules && sudo udevadm trigger\n"
            f"  Then log out and back in for the group to take effect.")

    def _ensure_uinput(self):
        if self._uinput_fd is not None:
            return self._uinput_fd
        ok, msg = self.uinput_status()
        if not ok:
            raise PermissionError(msg)

        fd = os.open(UINPUT_DEV, os.O_WRONLY | os.O_NONBLOCK)
        try:
            for ev in (EV_KEY, EV_ABS, EV_REL, EV_SYN):
                fcntl.ioctl(fd, UI_SET_EVBIT, ev)
            for btn in (BTN_LEFT, BTN_RIGHT, BTN_MIDDLE):
                fcntl.ioctl(fd, UI_SET_KEYBIT, btn)
            for axis in (ABS_X, ABS_Y):
                fcntl.ioctl(fd, UI_SET_ABSBIT, axis)
            fcntl.ioctl(fd, UI_SET_RELBIT, REL_WHEEL)

            absmax = [0] * 64
            absmax[ABS_X] = absmax[ABS_Y] = ABS_MAX
            payload = struct.pack(
                _UINPUT_USER_DEV_FMT,
                b"SOC Ultralight Virtual Pointer",
                0x03, 0x1234, 0x5678, 1,       # bustype USB, vendor, product, version
                0,                             # ff_effects_max
                *absmax, *([0] * 64), *([0] * 64), *([0] * 64))
            os.write(fd, payload)
            fcntl.ioctl(fd, UI_DEV_CREATE)
            time.sleep(0.15)                   # let udev settle before first event
        except Exception:
            os.close(fd)
            raise
        self._uinput_fd = fd
        return fd

    def _emit(self, events):
        """Write (type, code, value) triples plus a SYN_REPORT."""
        fd = self._ensure_uinput()
        blob = b"".join(struct.pack(_EVENT_FMT, 0, 0, t, c, v) for t, c, v in events)
        blob += struct.pack(_EVENT_FMT, 0, 0, EV_SYN, SYN_REPORT, 0)
        os.write(fd, blob)

    def click(self, button=BTN_LEFT):
        """Press+release at the current pointer position (extension)."""
        self._remote().click(button)

    def scroll(self, clicks):
        """Discrete wheel scroll (extension)."""
        self._remote().scroll(int(clicks))

    def key(self, keysym):
        """Press+release an X11 keysym (extension — SOC types into agents)."""
        self._remote().type_keysym(keysym)

    def _remote(self):
        if self._remote_session is None:
            from .wayland_remotedesktop import RemoteDesktopSession
            self._remote_session = RemoteDesktopSession()
        return self._remote_session

    @staticmethod
    def screen_locked():
        """True while the session is locked.

        GNOME inhibits ALL remote access while locked — gnome-shell enters a
        session mode with `allowScreencast` false and calls
        `inhibit_remote_access()`, after which every RemoteDesktop session
        creation fails with "Session creation inhibited". That is deliberate:
        it stops a background process typing into a locked machine.

        Injection therefore stops dead at lock, and callers must treat that as
        a normal paused state rather than an error. Cheap enough to poll.
        """
        import subprocess
        try:
            out = subprocess.run(
                ["gdbus", "call", "--session", "--dest", "org.gnome.ScreenSaver",
                 "--object-path", "/org/gnome/ScreenSaver", "--method",
                 "org.gnome.ScreenSaver.GetActive"],
                capture_output=True, text=True, timeout=5).stdout.strip()
            return out == "(true,)"
        except Exception:
            return False

    def input_status(self):
        """(ok, message) — whether pointer/keyboard injection is usable."""
        if self.screen_locked():
            return False, ("Screen is LOCKED — GNOME inhibits remote access "
                           "while locked, so pointer/keyboard injection is "
                           "unavailable until the session is unlocked. This is "
                           "by design, not a fault. Capture is also affected.")
        try:
            self._remote().start()
            return True, ("RemoteDesktop portal session active — pointer and "
                          "keyboard injection available.")
        except Exception as exc:
            return False, (
                f"RemoteDesktop portal unavailable: {exc}\n"
                f"  This prompt grants control of the pointer and keyboard, so "
                f"it must be accepted once by the operator. It is then cached "
                f"(persist_mode=2) and never asked again.")

    def close_uinput(self):
        if self._uinput_fd is not None:
            try:
                fcntl.ioctl(self._uinput_fd, UI_DEV_DESTROY)
            except Exception:
                pass
            os.close(self._uinput_fd)
            self._uinput_fd = None

    # ── Screen capture (extension — replaces mss/X11 for pipeline A) ─────────

    def _screencast(self):
        if self._portal is None:
            from .wayland_screencast import ScreenCastSession
            self._portal = ScreenCastSession()
        return self._portal

    def grab_screen(self, png_path=None):
        """Capture one frame via the portal; returns PNG bytes.

        Prompts once on first use, then reconnects silently using the cached
        restore token. Costs ~50 ms per frame once the stream is open.
        """
        return self._screencast().grab_png(png_path)

    def screen_changed(self):
        """Cheap poll: has the screen changed since the last call?

        Hashing a frame costs ~13 ms against ~400 ms for tesseract, so an OCR
        watcher should gate on this. Counter-intuitively, cropping to a smaller
        OCR region does NOT help — tesseract's cost tracks how much text it
        finds, not pixel count (a dense 960x540 crop measured slower than the
        whole 1920x1080 screen).
        """
        return self._screencast().changed()

    # ── Input hook (same fallback contract as the X11 backend) ───────────────

    def install_input_hook(self, mark_operator) -> bool:
        """False: no OS-level input hook on Wayland — callers use the
        cross-platform position-watcher fallback, exactly as on X11."""
        return False

    # ── Instance lock ────────────────────────────────────────────────────────

    def acquire_instance_lock(self, name: str) -> bool:
        """Abstract-namespace socket: auto-released on exit, leaves no file.

        Identical to the X11 backend — this is kernel-level and has nothing to
        do with the display server.
        """
        if self._lock_sock is not None:
            return True
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            sock.bind("\0" + name)
        except OSError:
            sock.close()
            return False
        self._lock_sock = sock
        return True

    # ── Console / taskbar identity: no-ops off win32 ─────────────────────────

    def hide_own_console(self):
        pass

    def set_app_id(self, app_id: str):
        pass
