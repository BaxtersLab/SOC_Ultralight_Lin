#!/usr/bin/env python3
"""Timer Prompt — fire a prompt into a target at a set time.

WHY THIS EXISTS
---------------
When a session hits its token ceiling the harness says how long until it
refreshes ("resets in 3h 24m"). That is dead time: the work is queued, the
operator is not. This widget takes that number, waits it out plus a few minutes
of grace, and types a prompt (default "proceed") into a chosen spot on screen —
so the flow picks itself back up while nobody is at the desk.

HOW IT INJECTS, AND WHY NOT xdotool
-----------------------------------
The sibling VS Mic Widget pastes with `xdotool getactivewindow`. That is X11
only. Under a native Wayland session XTEST reaches XWayland and no native
Wayland client ever sees the keystroke — it fails silently, which is exactly
how SOC's auto-click was broken for a week.

So this drives `pyautogui` **through SOC's platform layer**, the same seam SOC
itself uses. On Windows and X11 pyautogui is native; on Wayland
`platform_layer.wayland_shims` routes it to uinput and the portal. One code
path, three platforms, and the one that is hardest is already proven here.

The send sequence is lifted from SOC's own agent router rather than invented:
copy to clipboard, click the target, select-all, paste, Enter. Clipboard paste
beats synthetic per-character typing because it survives keyboard layouts,
accents, and newlines.

TARGET PICKING — WHY A DRAGGABLE MARKER
---------------------------------------
Wayland has no protocol to ask where the pointer is and no global input hook:
`position()` returns only what SOC last set, never the operator's physical
mouse. Reading the cursor to learn the target is impossible here.

The first attempt was a full-screen click-to-pick overlay at 25% alpha. **Do
not reintroduce it.** Tk's `-alpha` is not honoured for an `overrideredirect`
window under XWayland, so instead of dimming the desktop it painted it solid
black edge to edge, with the only exit a keystroke that an unfocused
override-redirect window may never receive. It looks exactly like the machine
has died.

What replaced it is a small always-on-top marker the operator drags over the
spot and confirms. It reports its own position, so it needs no pointer query
and no input hook; it never covers more than a corner of the screen; and the
coordinate can always be typed in by hand instead.
"""

import os
import sys
import threading
import time
from datetime import datetime, timedelta

import tkinter as tk

# Route pyautogui through the Wayland backend when that is the live session.
# Must happen before pyautogui is used. No-op on Windows and X11.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from platform_layer.wayland_shims import install as _install_wayland_shims
    _WAYLAND = bool(_install_wayland_shims())
except Exception as _e:                                    # pragma: no cover
    _WAYLAND = False
    print(f"[timer-prompt] wayland shim unavailable: {_e}")

import pyautogui                                           # noqa: E402
try:
    import pyperclip                                       # noqa: E402
except Exception:                                          # pragma: no cover
    pyperclip = None

# ── Colours (matched to VS Mic Widget so the cluster looks like one suite) ────
BG      = "#1e1e1e"
BG2     = "#2d2d2d"
FG      = "#d4d4d4"
DIM     = "#8a8a8a"
RED     = "#e05555"
GREEN   = "#4ec994"
ACCENT  = "#569cd6"
YELLOW  = "#dcdcaa"

# Seconds of visible, cancellable warning before the prompt actually fires.
# The operator may be back at the desk; typing into their window unannounced is
# not acceptable, and a countdown costs nothing when they are away.
ARM_WARNING_S = 10

# Delays copied from SOC's agent router — they exist because contenteditable
# chat boxes drop input sent too soon after a click.
CLICK_SETTLE_S = 0.35
BETWEEN_KEYS_S = 0.10
PASTE_SETTLE_S = 0.45

# Target-picker marker. Small on purpose: it floats over the operator's work.
MARKER_W, MARKER_H = 128, 34
CROSS_OFF = 9          # crosshair centre, offset from the marker's origin

STATE_FILE = os.path.join(
    os.path.expanduser("~"), ".soc-ultralight", "timer_prompt.json")


class TimerPrompt:
    def __init__(self, root):
        self.root = root
        self.target = None          # (x, y) or None
        self.deadline = None        # epoch seconds, or None when idle
        self.firing = False
        self._marker = None
        self._tick_job = None
        self._drag_x = 0
        self._drag_y = 0

        self._build_window()
        self._build_ui()
        self._load_state()
        self._tick()

    # ── window ───────────────────────────────────────────────────────────────
    def _build_window(self):
        self.root.title("Timer Prompt")
        self.root.attributes("-topmost", True)
        self.root.overrideredirect(True)
        self.root.configure(bg=BG)
        self.root.resizable(False, False)
        w, h = 236, 250
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        self.root.geometry(f"{w}x{h}+{(sw - w) // 2}+{(sh - h) // 2}")

    def _build_ui(self):
        bar = tk.Frame(self.root, bg=BG2, height=20)
        bar.pack(fill="x")
        bar.bind("<Button-1>", self._drag_start)
        bar.bind("<B1-Motion>", self._drag_move)
        lbl = tk.Label(bar, text="⏱ Timer Prompt", bg=BG2, fg=FG,
                       font=("Segoe UI", 8, "bold"))
        lbl.pack(side="left", padx=6, pady=1)
        lbl.bind("<Button-1>", self._drag_start)
        lbl.bind("<B1-Motion>", self._drag_move)
        tk.Button(bar, text="✕", bg=BG2, fg=RED, bd=0, font=("Segoe UI", 8),
                  activebackground=BG2, padx=2, pady=0,
                  command=self._quit).pack(side="right", padx=4)

        body = tk.Frame(self.root, bg=BG)
        body.pack(fill="both", expand=True, padx=7, pady=4)

        # Countdown — the one thing worth reading from across the room.
        self.count_lbl = tk.Label(body, text="—:—:—", bg=BG, fg=ACCENT,
                                  font=("Consolas", 17, "bold"))
        self.count_lbl.pack()
        self.fire_at_lbl = tk.Label(body, text="not armed", bg=BG, fg=DIM,
                                    font=("Segoe UI", 7))
        self.fire_at_lbl.pack(pady=(0, 4))

        # Duration + grace on one row. Grace fires a little AFTER the refresh,
        # never before, or the prompt lands on a still-exhausted session.
        self.h_var = tk.StringVar(value="0")
        self.m_var = tk.StringVar(value="0")
        self.grace_var = tk.StringVar(value="3")
        row = tk.Frame(body, bg=BG); row.pack(fill="x")

        def spin(parent, var, suffix):
            tk.Entry(parent, textvariable=var, width=2, bg=BG2, fg=FG,
                     insertbackground=FG, justify="center", relief="flat",
                     font=("Consolas", 9)).pack(side="left", padx=(3, 1))
            tk.Label(parent, text=suffix, bg=BG, fg=DIM,
                     font=("Segoe UI", 7)).pack(side="left")

        tk.Label(row, text="in", bg=BG, fg=FG,
                 font=("Segoe UI", 8)).pack(side="left")
        spin(row, self.h_var, "h")
        spin(row, self.m_var, "m")
        tk.Label(row, text="+", bg=BG, fg=DIM,
                 font=("Segoe UI", 8)).pack(side="left", padx=(5, 0))
        spin(row, self.grace_var, "m grace")

        # Target: marker button, typed x/y, and the live value.
        row3 = tk.Frame(body, bg=BG); row3.pack(fill="x", pady=(5, 0))
        tk.Button(row3, text="✛", bg=BG2, fg=FG, bd=0, padx=4, pady=0,
                  activebackground=ACCENT, font=("Segoe UI", 8),
                  command=self._pick_target).pack(side="left")
        self.x_var = tk.StringVar(value="")
        self.y_var = tk.StringVar(value="")
        for v in (self.x_var, self.y_var):
            e = tk.Entry(row3, textvariable=v, width=4, bg=BG2, fg=FG,
                         insertbackground=FG, justify="center", relief="flat",
                         font=("Consolas", 8))
            e.pack(side="left", padx=(3, 0))
            e.bind("<FocusOut>", lambda _e: self._apply_typed_target())
            e.bind("<Return>", lambda _e: self._apply_typed_target())
        self.target_lbl = tk.Label(row3, text="not set", bg=BG, fg=YELLOW,
                                   font=("Segoe UI", 7))
        self.target_lbl.pack(side="left", padx=5)

        # Prompt text
        self.text = tk.Text(body, height=2, bg=BG2, fg=FG, insertbackground=FG,
                            relief="flat", font=("Consolas", 9), wrap="word")
        self.text.pack(fill="x", pady=(5, 0))
        self.text.insert("1.0", "proceed")

        row_opt = tk.Frame(body, bg=BG); row_opt.pack(fill="x", pady=(2, 0))
        self.clear_var = tk.BooleanVar(value=True)
        tk.Checkbutton(row_opt, text="clear first", variable=self.clear_var,
                       bg=BG, fg=DIM, selectcolor=BG2, activebackground=BG,
                       activeforeground=FG, font=("Segoe UI", 7), bd=0,
                       padx=0, highlightthickness=0).pack(side="left")
        self.repeat_var = tk.BooleanVar(value=False)
        tk.Checkbutton(row_opt, text="repeat", variable=self.repeat_var, bg=BG,
                       fg=DIM, selectcolor=BG2, activebackground=BG,
                       activeforeground=FG, font=("Segoe UI", 7), bd=0,
                       padx=0, highlightthickness=0).pack(side="left", padx=(6, 0))
        backend = "wayland" if _WAYLAND else (
            "windows" if sys.platform == "win32" else "x11")
        tk.Label(row_opt, text=backend, bg=BG, fg=DIM,
                 font=("Segoe UI", 7)).pack(side="right")

        # Actions
        row4 = tk.Frame(body, bg=BG); row4.pack(fill="x", pady=(5, 0))
        self.arm_btn = tk.Button(row4, text="▶ Arm", bg=GREEN, fg="#12281f",
                                 bd=0, font=("Segoe UI", 9, "bold"), pady=1,
                                 command=self._arm)
        self.arm_btn.pack(side="left", fill="x", expand=True)
        tk.Button(row4, text="Test", bg=BG2, fg=FG, bd=0, pady=1,
                  font=("Segoe UI", 8),
                  command=self._test_now).pack(side="left", padx=(4, 0))

        self.status = tk.Label(body, text="", bg=BG, fg=DIM,
                               font=("Segoe UI", 7), wraplength=214,
                               justify="left", anchor="w")
        self.status.pack(fill="x", pady=(4, 0))

    # ── dragging ─────────────────────────────────────────────────────────────
    def _drag_start(self, e):
        self._drag_x, self._drag_y = e.x, e.y

    def _drag_move(self, e):
        self.root.geometry(
            f"+{e.x_root - self._drag_x}+{e.y_root - self._drag_y}")

    # ── target picking ───────────────────────────────────────────────────────
    def _pick_target(self):
        """Small draggable marker: put its crosshair on the spot, press Set.

        Deliberately NOT a full-screen overlay — see the module docstring. This
        covers a couple of hundred pixels, always has a visible Set and Cancel,
        and reports its own geometry, so it needs neither a pointer query nor an
        input hook.
        """
        if getattr(self, "_marker", None) is not None:
            return
        m = tk.Toplevel()
        self._marker = m
        m.attributes("-topmost", True)
        m.overrideredirect(True)
        m.configure(bg=ACCENT)
        m.geometry(f"{MARKER_W}x{MARKER_H}+300+300")

        # The crosshair sits at a known offset from the window origin, so the
        # operator can see roughly what they are aiming at instead of it being
        # hidden under the middle of the marker.
        cross = tk.Label(m, text="✛", bg=ACCENT, fg="#0d1b26",
                         font=("Segoe UI", 13, "bold"))
        cross.place(x=0, y=0, width=CROSS_OFF * 2, height=CROSS_OFF * 2)

        coord = tk.Label(m, text="", bg=ACCENT, fg="#0d1b26",
                         font=("Consolas", 8, "bold"))
        coord.place(x=CROSS_OFF * 2, y=2)

        def live(_e=None):
            coord.config(text=f"{m.winfo_rootx() + CROSS_OFF},"
                              f"{m.winfo_rooty() + CROSS_OFF}")

        def drag_start(e):
            m._dx, m._dy = e.x, e.y

        def drag_move(e):
            m.geometry(f"+{e.x_root - m._dx}+{e.y_root - m._dy}")
            live()

        for w in (m, cross, coord):
            w.bind("<Button-1>", drag_start)
            w.bind("<B1-Motion>", drag_move)

        def close():
            self._marker = None
            m.destroy()

        def confirm():
            self.target = (m.winfo_rootx() + CROSS_OFF, m.winfo_rooty() + CROSS_OFF)
            self.x_var.set(str(self.target[0]))
            self.y_var.set(str(self.target[1]))
            self._show_target()
            self._save_state()
            close()

        btns = tk.Frame(m, bg=ACCENT)
        btns.place(x=CROSS_OFF * 2, y=16)
        tk.Button(btns, text="Set", bg=GREEN, fg="#12281f", bd=0,
                  font=("Segoe UI", 8, "bold"), command=confirm).pack(side="left")
        tk.Button(btns, text="✕", bg=BG2, fg=RED, bd=0,
                  font=("Segoe UI", 8, "bold"), command=close).pack(side="left", padx=3)
        live()

    def _apply_typed_target(self):
        """Coordinates can always be typed — the marker is a convenience, not
        the only way in."""
        try:
            self.target = (int(self.x_var.get()), int(self.y_var.get()))
        except ValueError:
            self._set_status("x and y must be whole numbers", RED)
            return
        self._show_target()
        self._save_state()

    def _show_target(self):
        if self.target:
            self.target_lbl.config(text=f"{self.target[0]},{self.target[1]}", fg=GREEN)
        else:
            self.target_lbl.config(text="not set", fg=YELLOW)

    # ── arming / countdown ───────────────────────────────────────────────────
    def _arm(self):
        if self.deadline is not None:
            self.deadline = None
            self.arm_btn.config(text="▶ Arm", bg=GREEN, fg="#12281f")
            self._set_status("cancelled", DIM)
            self._save_state()
            return
        if not self.target:
            self._set_status("set a target first — the prompt needs somewhere to land", RED)
            return
        try:
            secs = (int(self.h_var.get() or 0) * 3600
                    + int(self.m_var.get() or 0) * 60
                    + int(self.grace_var.get() or 0) * 60)
        except ValueError:
            self._set_status("hours, minutes and grace must be whole numbers", RED)
            return
        if secs <= 0:
            self._set_status("that time has already passed", RED)
            return
        self.deadline = time.time() + secs
        self.arm_btn.config(text="■ Cancel", bg=RED, fg="#2a1212")
        fire_at = datetime.now() + timedelta(seconds=secs)
        self.fire_at_lbl.config(text=f"fires at {fire_at.strftime('%H:%M:%S')}")
        self._set_status("armed", GREEN)
        self._save_state()

    def _tick(self):
        if self.deadline is not None and not self.firing:
            left = self.deadline - time.time()
            if left <= 0:
                self._fire()
            else:
                h, rem = divmod(int(left), 3600)
                m, s = divmod(rem, 60)
                self.count_lbl.config(
                    text=f"{h:d}:{m:02d}:{s:02d}",
                    fg=YELLOW if left <= ARM_WARNING_S else ACCENT)
                if left <= ARM_WARNING_S:
                    self._set_status(
                        f"firing in {int(left)}s — press Cancel to stop", YELLOW)
        self._tick_job = self.root.after(250, self._tick)

    # ── firing ───────────────────────────────────────────────────────────────
    def _test_now(self):
        if not self.target:
            self._set_status("set a target first", RED)
            return
        self._fire(test=True)

    def _fire(self, test=False):
        if self.firing:
            return
        self.firing = True
        if not test:
            self.deadline = None
            self.arm_btn.config(text="▶ Arm", bg=GREEN, fg="#12281f")
        prompt = self.text.get("1.0", "end-1c")
        target = self.target
        clear_first = bool(self.clear_var.get())

        # Hide, so the widget itself can never be what gets clicked.
        self.root.withdraw()

        def work():
            ok, err = self._send(target, prompt, clear_first)
            self.root.after(0, lambda: self._fired(ok, err, test))

        # pyautogui blocks for a second or two; running it on the Tk thread
        # would freeze the countdown mid-fire. All UI updates come back through
        # root.after — Tk objects are never touched off the main thread.
        threading.Thread(target=work, daemon=True).start()

    def _send(self, target, prompt, clear_first):
        """SOC's proven order: copy → click → select-all → paste → Enter."""
        try:
            if pyperclip is not None:
                pyperclip.copy(prompt)
            else:
                return False, "pyperclip missing — cannot place text on the clipboard"
            pyautogui.click(target[0], target[1])
            time.sleep(CLICK_SETTLE_S)
            if clear_first:
                pyautogui.hotkey("ctrl", "a")
                time.sleep(BETWEEN_KEYS_S)
            pyautogui.hotkey("ctrl", "v")
            time.sleep(PASTE_SETTLE_S)
            pyautogui.press("enter")
            return True, ""
        except Exception as e:
            return False, str(e)

    def _fired(self, ok, err, test):
        self.firing = False
        self.root.deiconify()
        stamp = datetime.now().strftime("%H:%M:%S")
        if ok:
            self._set_status(
                f"{'test ' if test else ''}sent at {stamp}", GREEN)
            self._log(f"{stamp} sent to {self.target}: {self.text.get('1.0','end-1c')[:60]}")
            if not test and self.repeat_var.get():
                # Re-arm for the same interval. Token ceilings recur, so the
                # useful default for a repeat is "the same wait again".
                self._arm()
        else:
            self._set_status(f"failed: {err}", RED)
            self._log(f"{stamp} FAILED: {err}")
        self._save_state()

    # ── persistence / misc ───────────────────────────────────────────────────
    def _set_status(self, msg, colour=FG):
        self.status.config(text=msg, fg=colour)

    def _log(self, line):
        try:
            os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
            with open(os.path.join(os.path.dirname(STATE_FILE),
                                   "timer_prompt.log"), "a") as f:
                f.write(line + "\n")
        except Exception:
            pass

    def _save_state(self):
        import json
        try:
            os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
            with open(STATE_FILE, "w") as f:
                json.dump({
                    "target": self.target,
                    "prompt": self.text.get("1.0", "end-1c"),
                    "hours": self.h_var.get(),
                    "minutes": self.m_var.get(),
                    "grace": self.grace_var.get(),
                    "repeat": bool(self.repeat_var.get()),
                    "clear_first": bool(self.clear_var.get()),
                }, f, indent=2)
        except Exception:
            pass

    def _load_state(self):
        import json
        try:
            with open(STATE_FILE) as f:
                s = json.load(f)
        except Exception:
            return
        if isinstance(s.get("target"), list) and len(s["target"]) == 2:
            self.target = (int(s["target"][0]), int(s["target"][1]))
            self.x_var.set(str(self.target[0]))
            self.y_var.set(str(self.target[1]))
            self._show_target()
        if s.get("prompt"):
            self.text.delete("1.0", "end")
            self.text.insert("1.0", s["prompt"])
        self.h_var.set(s.get("hours", "0"))
        self.m_var.set(s.get("minutes", "0"))
        self.grace_var.set(s.get("grace", "3"))
        self.repeat_var.set(bool(s.get("repeat", False)))
        self.clear_var.set(bool(s.get("clear_first", True)))

    def _quit(self):
        self._save_state()
        if self._tick_job:
            self.root.after_cancel(self._tick_job)
        # Deterministic teardown. A Tk object finalized on a non-main thread
        # aborts the process with Tcl_AsyncDelete — the sibling Hot Rod Tuner
        # hit exactly that and it is worth never repeating.
        self.root.quit()
        self.root.destroy()


def main():
    root = tk.Tk()
    TimerPrompt(root)
    root.mainloop()


if __name__ == "__main__":
    main()
