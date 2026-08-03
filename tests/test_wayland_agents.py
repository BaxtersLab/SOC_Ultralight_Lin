"""Pure-logic tests for the Wayland agent-window tracker.

Everything here runs without a display, a portal or a compositor: the parts
that need those are proven by soc_port/verify_agent_tracking.py, which drives a
real window end to end.
"""

import io
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))


def _png(w, h, fill=0, box=None, boxfill=200, noise=False):
    from PIL import Image, ImageDraw
    im = Image.new("RGB", (w, h), (fill, fill, fill))
    if box:
        ImageDraw.Draw(im).rectangle(box, fill=(boxfill, boxfill, boxfill))
    if noise:
        import random
        rnd = random.Random(0)
        px = im.load()
        for y in range(0, h, 3):
            for x in range(0, w, 3):
                v = rnd.randrange(256)
                px[x, y] = (v, v, v)
    out = io.BytesIO()
    im.save(out, format="PNG")
    return out.getvalue()


class ContentBoundsTests(unittest.TestCase):
    """GNOME pads window streams into a monitor-sized frame."""

    @classmethod
    def setUpClass(cls):
        try:
            from platform_layer.wayland_agents import AgentWindow
        except Exception as exc:
            raise unittest.SkipTest(f"cannot import wayland_agents: {exc}")
        cls.AW = AgentWindow

    def test_finds_window_content_inside_padded_frame(self):
        """A 1200x800 window inside a 1920x1080 frame must measure as 1200x800.

        The portal reports only the BUFFER size, so the real window size has to
        come from the content bounds. Measured on GNOME 50: 46% non-black.
        """
        png = _png(1920, 1080, fill=0, box=(0, 0, 1199, 799))
        self.assertEqual(self.AW._content_bounds(png), (0, 0, 1200, 800))

    def test_content_at_origin(self):
        png = _png(400, 300, fill=0, box=(0, 0, 199, 149))
        x0, y0, _, _ = self.AW._content_bounds(png)
        self.assertEqual((x0, y0), (0, 0))

    def test_entirely_blank_frame_is_none(self):
        """A blank capture must be reported, not silently treated as a window."""
        self.assertIsNone(self.AW._content_bounds(_png(320, 240, fill=0)))

    def test_unpadded_frame_returns_full_size(self):
        png = _png(640, 480, fill=200)
        self.assertEqual(self.AW._content_bounds(png), (0, 0, 640, 480))


class PatchSelectionTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        try:
            from platform_layer.wayland_agents import AgentWindow
        except Exception as exc:
            raise unittest.SkipTest(f"cannot import wayland_agents: {exc}")
        cls.AW = AgentWindow

    def _gray(self, png):
        return self.AW._decode(png)

    def test_rejects_flat_tiles(self):
        """A flat tile correlates ~1.0 against ANY flat screen region.

        That is the failure that made four blank button templates match empty
        desktop at 0.89-1.00. Patch selection must not return one when a
        textured alternative exists.
        """
        import numpy as np
        png = _png(600, 400, fill=30, box=(0, 0, 200, 200), boxfill=0, noise=True)
        patches = self.AW._patches(self._gray(png))
        self.assertTrue(patches)
        for _, _, tile in patches:
            self.assertGreater(float(np.var(tile)), 25.0,
                               "a featureless tile was selected")

    def test_returns_several_candidates(self):
        """Any single patch may be occluded by another window."""
        png = _png(800, 600, fill=40, noise=True)
        self.assertGreater(len(self.AW._patches(self._gray(png))), 1)

    def test_tiny_window_still_yields_a_patch(self):
        """Smaller than PATCH must degrade gracefully, not crash."""
        png = _png(80, 60, fill=40, noise=True)
        patches = self.AW._patches(self._gray(png))
        self.assertEqual(len(patches), 1)


class CalibrationStateTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        try:
            from platform_layer import wayland_agents as wa
        except Exception as exc:
            raise unittest.SkipTest(f"cannot import wayland_agents: {exc}")
        cls.wa = wa

    def test_agent_ids_exclude_agent4(self):
        """agent4 (A4v) is not click-calibrated; soc_ultralight's
        _apply_template_match likewise handles only agent1/2/3/5."""
        self.assertNotIn("agent4", self.wa.AGENT_IDS)
        self.assertEqual(self.wa.AGENT_IDS, ("agent1", "agent2", "agent3", "agent5"))

    def test_unknown_agent_rejected(self):
        with self.assertRaises(ValueError):
            self.wa.agent_window("agent9")

    def test_each_agent_has_its_own_reference(self):
        """A reference (and token) is bound to what the operator picked, so
        sharing one between agents would track the wrong window."""
        paths = {self.wa.AgentWindow(a).reference_path for a in self.wa.AGENT_IDS}
        self.assertEqual(len(paths), len(self.wa.AGENT_IDS))

    def test_uncalibrated_agent_raises_rather_than_guessing(self):
        w = self.wa.AgentWindow("agent3")
        if w.is_calibrated():
            self.skipTest("agent3 is calibrated on this machine")
        with self.assertRaises(RuntimeError):
            w.reference_size


if __name__ == "__main__":
    unittest.main()
