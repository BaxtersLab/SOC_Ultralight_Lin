#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Regression tests for Timer Prompt's window identity.

Timer Prompt showed a generic gear on the taskbar. Three separate causes had
to hold at once, so all three are asserted here: a window icon, a WM_CLASS the
launcher can match, and a .desktop entry that points at the wrapper script.

Run from the repo root:

    ./.venv/bin/python -m pytest tests/test_timer_prompt_icon.py -q
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

REPO = Path(__file__).resolve().parents[1]

import timer_prompt


class TimerPromptIconTests(unittest.TestCase):
    """Timer Prompt showed a generic gear: no window icon, and WM_CLASS "Tk".
    Both halves are needed -- a launcher Icon= alone dresses the dash entry,
    not the mapped window."""

    @classmethod
    def setUpClass(cls):
        cls.tp = timer_prompt

    def test_icon_assets_exist_at_every_size(self):
        for size in (16, 24, 32, 48, 64, 128, 256):
            f = self.tp.ICON_DIR / f"timer-prompt-{size}.png"
            self.assertTrue(f.exists(), f"missing {f.name}")

    def test_icons_are_square_and_correctly_sized(self):
        from PIL import Image
        for size in (16, 24, 32, 48, 64, 128, 256):
            im = Image.open(self.tp.ICON_DIR / f"timer-prompt-{size}.png")
            self.assertEqual(im.size, (size, size))

    def test_window_sets_an_icon(self):
        self.assertTrue(hasattr(self.tp.TimerPrompt, "_set_icon"))
        import inspect
        src = inspect.getsource(self.tp.TimerPrompt._build_window)
        self.assertIn("_set_icon", src, "_build_window never sets the icon")

    def test_entry_point_sets_wm_class(self):
        """Bare tk.Tk() yields WM_CLASS 'Tk' and the shell shows a gear."""
        import inspect
        src = inspect.getsource(self.tp.main)
        self.assertIn('className="timer-prompt"', src)

    def test_desktop_entry_matches_the_wm_class(self):
        entry = REPO / "packaging" / "timer-prompt.desktop"
        self.assertTrue(entry.exists(), "packaging/timer-prompt.desktop missing")
        text = entry.read_text()
        self.assertIn("StartupWMClass=timer-prompt", text)
        self.assertIn("Icon=timer-prompt", text)
        # Must launch through the wrapper, or pyautogui falls back to XTEST
        # and the typed prompt reaches no native Wayland client.
        self.assertIn("timer_prompt.sh", text)
        self.assertNotIn("Exec=/home/baxter/workspace/SOC_Ultralight/timer_prompt.py", text)

    def test_module_actually_imports(self):
        """Regression: ICON_DIR used Path before pathlib was imported.
        py_compile passed; importing raised NameError."""
        self.assertTrue(str(self.tp.ICON_DIR).endswith("assets"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
