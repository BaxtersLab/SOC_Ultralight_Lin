"""Route pyautogui and PIL.ImageGrab through the Wayland desktop stream.

WHY AT THIS LAYER
-----------------
SOC drives the desktop through pyautogui (33 call sites) and reads it through
PIL.ImageGrab / mss (8 call sites). Neither works on Wayland: pyautogui's
backend is XTest, and ImageGrab/mss capture the X root window. Under XWayland
both see only XWayland clients — never native Wayland windows, and never the
real desktop — so they fail silently by returning a black or partial image and
clicking into nothing.

Rewriting 41 call sites would be churn for no gain. SOC already treats
pyautogui as a seam: `_hands_wrap` replaces those same functions to enforce the
operator-yield rule. Installing a Wayland backend UNDER that wrapper keeps the
hands guard intact and leaves the calling code untouched.

`install()` is a no-op unless the Wayland backend is actually selected, so
importing this module is safe on win32 and X11.

WHAT IS AND IS NOT SHIMMED
--------------------------
Shimmed: moveTo, click, mouseDown/mouseUp, scroll, position, size, and the
keyboard entry points (press, keyDown/keyUp, write/typewrite, hotkey), plus
ImageGrab.grab.

NOT shimmed, because Wayland has no equivalent and a fake would be worse than
an error: anything needing another application's window — see
WaylandPlatform.UNSUPPORTED. Those callers must go through
wayland_agents.AgentWindow instead.

`position()` deserves note: Wayland has no protocol to query the pointer, so it
returns the last position SOC itself set. It does NOT track the operator's
physical mouse — nothing on Wayland can. SOC's attribution logic already
handles a missing hook (install_input_hook returns False and it falls back to
its own watcher), which is why this is safe rather than silently wrong.
"""

import os

_installed = False
_last_pos = (0, 0)


def _enabled():
    return os.environ.get("SOC_PLATFORM", "").strip().lower() == "wayland"


# ── keysym mapping ───────────────────────────────────────────────────────────

#: pyautogui key names -> X keysym names, where they differ.
_KEYNAMES = {
    "enter": "Return", "return": "Return", "esc": "Escape", "escape": "Escape",
    "tab": "Tab", "backspace": "BackSpace", "del": "Delete", "delete": "Delete",
    "space": "space", "up": "Up", "down": "Down", "left": "Left",
    "right": "Right", "home": "Home", "end": "End", "pageup": "Prior",
    "pagedown": "Next", "insert": "Insert",
    "ctrl": "Control_L", "ctrlleft": "Control_L", "ctrlright": "Control_R",
    "alt": "Alt_L", "altleft": "Alt_L", "altright": "Alt_R",
    "shift": "Shift_L", "shiftleft": "Shift_L", "shiftright": "Shift_R",
    "win": "Super_L", "super": "Super_L", "command": "Super_L",
    "capslock": "Caps_Lock",
}
_KEYNAMES.update({f"f{i}": f"F{i}" for i in range(1, 25)})


def _keysym(key):
    """pyautogui key name or single character -> X keysym int, or None."""
    from Xlib import XK
    name = _KEYNAMES.get(str(key).lower())
    if name is None:
        if len(str(key)) == 1:
            sym = XK.string_to_keysym(str(key))
            if sym:
                return sym
            return ord(str(key))          # Unicode fallback per the X spec
        name = str(key)
    return XK.string_to_keysym(name) or None


# ── installation ─────────────────────────────────────────────────────────────

def install(force=False):
    """Point pyautogui and ImageGrab at the Wayland desktop. Idempotent."""
    global _installed
    if _installed or not (force or _enabled()):
        return False

    import pyautogui
    from PIL import ImageGrab

    from .wayland_agents import desktop
    from .wayland_remotedesktop import BTN_LEFT, BTN_MIDDLE, BTN_RIGHT

    buttons = {"left": BTN_LEFT, "right": BTN_RIGHT, "middle": BTN_MIDDLE}

    def _resolve(x, y):
        global _last_pos
        if x is None or y is None:
            return _last_pos
        return int(x), int(y)

    # ── mouse ────────────────────────────────────────────────────────────────

    def moveTo(x=None, y=None, duration=0, *a, **kw):
        global _last_pos
        pos = _resolve(x, y)
        desktop().move_to(*pos)
        _last_pos = pos

    def click(x=None, y=None, clicks=1, interval=0.0, button="left", *a, **kw):
        import time
        global _last_pos
        pos = _resolve(x, y)
        code = buttons.get(button, BTN_LEFT)
        for i in range(max(int(clicks), 1)):
            desktop().click(pos[0], pos[1], code)
            if i + 1 < clicks and interval:
                time.sleep(interval)
        _last_pos = pos

    def mouseDown(x=None, y=None, button="left", *a, **kw):
        global _last_pos
        pos = _resolve(x, y)
        desktop().move_to(*pos)
        desktop()._session.button(buttons.get(button, BTN_LEFT), True)
        _last_pos = pos

    def mouseUp(x=None, y=None, button="left", *a, **kw):
        pos = _resolve(x, y)
        desktop()._session.button(buttons.get(button, BTN_LEFT), False)

    def scroll(clicks, x=None, y=None, *a, **kw):
        if x is not None and y is not None:
            moveTo(x, y)
        # pyautogui: positive scrolls UP. The portal's discrete axis is
        # positive-DOWN, so the sign is inverted here rather than at every
        # call site.
        desktop().scroll(-int(clicks))

    def position():
        """Last position SOC set. Wayland cannot report the real pointer."""
        return _last_pos

    def size():
        w, h = desktop().size or (0, 0)
        return (w, h)

    # ── keyboard ─────────────────────────────────────────────────────────────

    def keyDown(key, *a, **kw):
        sym = _keysym(key)
        if sym:
            desktop()._session.keysym(sym, True)

    def keyUp(key, *a, **kw):
        sym = _keysym(key)
        if sym:
            desktop()._session.keysym(sym, False)

    def press(keys, presses=1, interval=0.0, *a, **kw):
        import time
        seq = [keys] if isinstance(keys, str) else list(keys)
        for _ in range(max(int(presses), 1)):
            for k in seq:
                sym = _keysym(k)
                if sym:
                    desktop().key(sym)
            if interval:
                time.sleep(interval)

    def write(message, interval=0.0, *a, **kw):
        import time
        for ch in str(message):
            sym = _keysym(ch)
            if sym:
                desktop().key(sym)
            if interval:
                time.sleep(interval)

    def hotkey(*keys, **kw):
        """Chord: press modifiers in order, then release in reverse."""
        syms = [s for s in (_keysym(k) for k in keys) if s]
        for s in syms:
            desktop()._session.keysym(s, True)
        for s in reversed(syms):
            desktop()._session.keysym(s, False)

    for name, fn in (("moveTo", moveTo), ("click", click),
                     ("mouseDown", mouseDown), ("mouseUp", mouseUp),
                     ("scroll", scroll), ("position", position), ("size", size),
                     ("keyDown", keyDown), ("keyUp", keyUp), ("press", press),
                     ("write", write), ("typewrite", write), ("hotkey", hotkey)):
        setattr(pyautogui, name, fn)

    # ── screen capture ───────────────────────────────────────────────────────

    def grab(bbox=None, include_layered_windows=False, all_screens=False,
             xdisplay=None):
        """ImageGrab.grab replacement reading the Wayland desktop stream."""
        import io

        from PIL import Image
        img = Image.open(io.BytesIO(desktop().frame())).convert("RGB")
        return img.crop(bbox) if bbox else img

    ImageGrab.grab = grab

    _installed = True
    return True


def installed():
    return _installed
