#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Regression tests for the guarded auto-click mechanism.

SOC ships NO verifiers and knows nothing about what any of them check -- these
tests exercise the mechanism with throwaway policy written into a temp dir.
Every test that matters asserts a REFUSAL: a guard that fails open is worse
than no guard at all, because the templates it protects are the expensive ones.

    ./.venv/bin/python -m pytest tests/test_autoclick_guard.py -q
"""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import soc_ultralight as soc

REPO = Path(__file__).resolve().parents[1]


def guard_dir(rules, module_src=None, module_name="probe_guard"):
    """A throwaway guard directory: manifest + optional verifier module."""
    d = Path(tempfile.mkdtemp())
    (d / "manifest.json").write_text(json.dumps(rules), encoding="utf-8")
    if module_src is not None:
        (d / f"{module_name}.py").write_text(module_src, encoding="utf-8")
    return d


RULE = [{"pattern": "sensitive", "module": "probe_guard", "callable": "verify"}]


class ManifestTests(unittest.TestCase):
    def test_absent_manifest_yields_no_rules_and_no_error(self):
        missing = Path(tempfile.mkdtemp()) / "manifest.json"
        self.assertEqual(soc.load_guard_rules(missing), [])
        self.assertEqual(soc.guard_error(missing), "")

    def test_valid_manifest_loads(self):
        d = guard_dir(RULE)
        self.assertEqual(len(soc.load_guard_rules(d / "manifest.json")), 1)
        self.assertEqual(soc.guard_error(d / "manifest.json"), "")

    def test_malformed_manifest_is_reported_not_silently_empty(self):
        """One typo must not disarm every guard without saying so."""
        d = Path(tempfile.mkdtemp())
        (d / "manifest.json").write_text("{not json", encoding="utf-8")
        self.assertEqual(soc.load_guard_rules(d / "manifest.json"), [])
        self.assertIn("NOTHING is guarded", soc.guard_error(d / "manifest.json"))

    def test_non_list_manifest_is_reported(self):
        d = guard_dir({"pattern": "x"})
        self.assertIn("must be a JSON list", soc.guard_error(d / "manifest.json"))

    def test_incomplete_rule_is_dropped_and_reported(self):
        d = guard_dir([{"pattern": "sensitive"}])
        self.assertEqual(soc.load_guard_rules(d / "manifest.json"), [])
        self.assertIn("were skipped", soc.guard_error(d / "manifest.json"))


class RuleMatchingTests(unittest.TestCase):
    def test_matches_on_a_normalised_stem(self):
        """The operator names templates by hand; a near-miss on case or
        punctuation must not downgrade a guarded button to an ordinary one."""
        rules = soc.load_guard_rules(guard_dir(RULE) / "manifest.json")
        for stem in ("sensitive_button", "Sensitive-Button", "MY_SENSITIVE_ok",
                     "sensitive"):
            self.assertIsNotNone(soc.guard_rule_for(stem, rules), stem)

    def test_unrelated_templates_are_not_guarded(self):
        rules = soc.load_guard_rules(guard_dir(RULE) / "manifest.json")
        for stem in ("agent1_send", "VS_code_allow", "keep_all_changes"):
            self.assertIsNone(soc.guard_rule_for(stem, rules), stem)

    def test_no_rules_means_nothing_is_guarded(self):
        self.assertIsNone(soc.guard_rule_for("sensitive_button", []))


class VerifierLoadingTests(unittest.TestCase):
    OK = "def verify(screen, rect):\n    return (True, 'ok')\n"
    NO = "def verify(screen, rect):\n    return (False, 'nope')\n"
    BOOM = "def verify(screen, rect):\n    raise RuntimeError('policy broke')\n"

    def load(self, module_src, rules=None):
        d = guard_dir(rules or RULE, module_src)
        rule = soc.load_guard_rules(d / "manifest.json")[0]
        with patch.object(soc, "GUARD_DIR", d):
            return soc.load_guard_callable(rule)

    def test_a_valid_verifier_loads_and_runs(self):
        self.assertEqual(self.load(self.OK)(None, None), (True, "ok"))

    def test_a_refusing_verifier_refuses(self):
        self.assertEqual(self.load(self.NO)(None, None), (False, "nope"))

    def test_missing_module_raises_rather_than_returning_none(self):
        """The caller treats an exception as a refusal. Returning None would
        be read as 'no guard' and the click would go through."""
        d = guard_dir(RULE)                       # manifest but no module file
        rule = soc.load_guard_rules(d / "manifest.json")[0]
        with patch.object(soc, "GUARD_DIR", d):
            with self.assertRaises(FileNotFoundError):
                soc.load_guard_callable(rule)

    def test_missing_callable_raises(self):
        d = guard_dir(RULE, "def something_else(a, b):\n    return (True, '')\n")
        rule = soc.load_guard_rules(d / "manifest.json")[0]
        with patch.object(soc, "GUARD_DIR", d):
            with self.assertRaises(AttributeError):
                soc.load_guard_callable(rule)

    def test_a_raising_verifier_propagates(self):
        fn = self.load(self.BOOM)
        with self.assertRaises(RuntimeError):
            fn(None, None)


class LoopWiringTests(unittest.TestCase):
    """The mechanism only helps if the loop actually consults it."""

    def test_guard_is_resolved_before_the_click(self):
        import inspect
        src = inspect.getsource(soc.SOCUltralight._autoclick_loop)
        self.assertIn("guard_rule_for", src)
        self.assertLess(src.index("guard_rule_for"), src.index("pyautogui.click"),
                        "guard is consulted after the click")

    def test_a_failed_guard_load_blocks_rather_than_allows(self):
        import inspect
        src = inspect.getsource(soc.SOCUltralight._autoclick_loop)
        i = src.index("load_guard_callable")
        window = src[i:i + 700]
        self.assertIn("allowed = False", window,
                      "an unloadable guard must refuse, never allow")

    def test_soc_ships_no_verifiers_of_its_own(self):
        """SOC must not carry operator policy. If this fails, a site decision
        has been baked into a public tool."""
        r = subprocess.run(["git", "ls-files"], cwd=REPO,
                           capture_output=True, text=True)
        offenders = [ln for ln in r.stdout.splitlines()
                     if "guards/" in ln or ln.endswith("_guard.py")]
        self.assertEqual(offenders, [], f"verifier tracked in the repo: {offenders}")

    def test_guard_policy_lives_outside_the_repo(self):
        for pth in (soc.GUARD_DIR, soc.GUARD_MANIFEST):
            self.assertNotIn(REPO, pth.parents, f"{pth} is inside the repo")


class PrivateTemplateTests(unittest.TestCase):
    """buttons database/private/ — scanned, never committed."""

    def test_private_dir_is_under_the_template_dir(self):
        self.assertEqual(soc.PRIVATE_TEMPLATE_DIR.parent, soc.TEMPLATE_DIR)

    def test_git_ignores_the_whole_private_directory(self):
        for name in ("private/anything.png", "private/x.PNG",
                     "private/deep/nested.png"):
            r = subprocess.run(["git", "check-ignore", f"buttons database/{name}"],
                               cwd=REPO, capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, f"git would TRACK {name}")

    def test_nothing_from_private_is_tracked(self):
        r = subprocess.run(["git", "ls-files", "buttons database/private"],
                           cwd=REPO, capture_output=True, text=True)
        self.assertEqual(r.stdout.strip(), "")

    def test_discovery_includes_private_templates(self):
        import inspect
        src = inspect.getsource(soc.template_pngs)
        self.assertIn("PRIVATE_TEMPLATE_DIR", src)

    def test_template_path_resolves_private_stems(self):
        import inspect
        src = inspect.getsource(soc.SOCUltralight._template_path)
        self.assertIn("PRIVATE_TEMPLATE_DIR", src)


if __name__ == "__main__":
    unittest.main(verbosity=2)
