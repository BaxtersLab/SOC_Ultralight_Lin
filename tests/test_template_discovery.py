"""Template discovery must be case-insensitive (Linux port regression).

Windows filesystems are case-insensitive, so `TEMPLATE_DIR.glob("*.png")` there
matches `Agent2_allow.PNG` as well. Linux (ext4) is case-sensitive and it does
not — half the shipped "buttons database" library uses an uppercase `.PNG`
extension, so 20 of 40 templates were silently invisible to calibration and
auto-click, including Send_message_to_Agent1.PNG and VS_code_allow.PNG.

These tests fail on the old `glob("*.png")` implementation and pass on
`template_pngs()`.
"""

import importlib.util
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def _load_soc():
    """Import soc_ultralight without executing its Tk main loop."""
    spec = importlib.util.spec_from_file_location(
        "soc_ultralight_under_test", REPO / "soc_ultralight.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


class TemplateDiscoveryTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        try:
            cls.soc = _load_soc()
        except Exception as exc:            # GUI/deps unavailable in this env
            raise unittest.SkipTest(f"cannot import soc_ultralight: {exc}")

    def test_finds_uppercase_png_extension(self):
        """A .PNG file must be discovered, not just .png."""
        tmp = Path(self.id().replace(".", "_") + "_dir")
        tmp.mkdir(exist_ok=True)
        try:
            (tmp / "lower_case.png").write_bytes(b"x")
            (tmp / "UPPER_CASE.PNG").write_bytes(b"x")
            (tmp / "Mixed_Case.Png").write_bytes(b"x")
            names = {p.name for p in self.soc.template_pngs(tmp)}
            self.assertEqual(
                names, {"lower_case.png", "UPPER_CASE.PNG", "Mixed_Case.Png"},
                "template discovery is case-sensitive — uppercase .PNG "
                "templates are invisible on Linux")
        finally:
            for f in tmp.iterdir():
                f.unlink()
            tmp.rmdir()

    def test_skips_subdirectories(self):
        """Retired//archive folders must not be loaded as templates."""
        tmp = Path(self.id().replace(".", "_") + "_dir")
        (tmp / "_retired").mkdir(parents=True, exist_ok=True)
        try:
            (tmp / "real.png").write_bytes(b"x")
            (tmp / "_retired" / "old.png").write_bytes(b"x")
            names = {p.name for p in self.soc.template_pngs(tmp)}
            self.assertEqual(names, {"real.png"})
        finally:
            (tmp / "_retired" / "old.png").unlink()
            (tmp / "_retired").rmdir()
            (tmp / "real.png").unlink()
            tmp.rmdir()

    def test_missing_directory_is_empty_not_an_exception(self):
        self.assertEqual(self.soc.template_pngs(Path("no_such_dir_here")), [])

    def test_shipped_library_is_fully_visible(self):
        """Every PNG in buttons database/ must be discoverable."""
        d = REPO / "buttons database"
        if not d.is_dir():
            self.skipTest("buttons database/ not present")
        on_disk = {p.name for p in d.iterdir()
                   if p.is_file() and p.suffix.lower() == ".png"}
        found = {p.name for p in self.soc.template_pngs(d)}
        self.assertEqual(found, on_disk,
                         f"{len(on_disk) - len(found)} template(s) on disk are "
                         f"not being loaded")



class AutoclickTemplatePathTests(unittest.TestCase):
    """Auto-click must find a template whatever the extension's case.

    The loop used to rebuild the path as f"{stem}.png". Windows matches either
    spelling; ext4 does not, so the 19 uppercase .PNG templates were invisible
    to the scan while still being listed in the panel — the operator could
    enable one and nothing would ever happen, with no error anywhere.
    """

    @classmethod
    def setUpClass(cls):
        try:
            import soc_ultralight as soc
        except Exception as exc:
            raise unittest.SkipTest(f"cannot import soc_ultralight: {exc}")
        cls.soc = soc

    def test_resolves_both_cases(self):
        d = self.soc.TEMPLATE_DIR
        if not d.is_dir():
            self.skipTest("buttons database/ not present")
        stems = [p.stem for p in self.soc.template_pngs(d)]
        if not stems:
            self.skipTest("no templates on disk")
        unresolved = [st for st in stems
                      if self.soc.SOCUltralight._template_path(st) is None]
        self.assertEqual(unresolved, [],
                         f"{len(unresolved)} template(s) cannot be resolved by "
                         f"the auto-click loop")

    def test_uppercase_templates_are_reachable(self):
        d = self.soc.TEMPLATE_DIR
        if not d.is_dir():
            self.skipTest("buttons database/ not present")
        upper = [p for p in self.soc.template_pngs(d) if p.suffix == ".PNG"]
        if not upper:
            self.skipTest("no uppercase templates in this library")
        for p in upper:
            with self.subTest(template=p.name):
                self.assertIsNotNone(
                    self.soc.SOCUltralight._template_path(p.stem),
                    f"{p.name} is listed but unreachable — the exact bug that "
                    f"silently disabled {len(upper)} auto-click templates")

    def test_unknown_stem_returns_none_rather_than_a_bad_path(self):
        self.assertIsNone(
            self.soc.SOCUltralight._template_path("definitely_not_a_template_xyz"))


if __name__ == "__main__":
    unittest.main()
