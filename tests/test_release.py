from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "check_release.py"
SPEC = importlib.util.spec_from_file_location("check_release_under_test", SCRIPT)
assert SPEC and SPEC.loader
release = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = release
SPEC.loader.exec_module(release)


class ReleaseVersionTest(unittest.TestCase):
    distributed = ["plugins/codex-task-routing/scripts/routing.py"]

    def test_requires_strictly_higher_version_for_distributed_changes(self) -> None:
        cases = [
            ("1.2.3", "1.2.3", True),
            ("2.0.0", "1.99.99", True),
            ("1.2.3", "1.2.4", False),
            ("1.2.3", "2.0.0", False),
            ("1.2.3+old", "1.2.3+new", True),
            ("1.2.3", "1.2.3+build.1", True),
        ]
        for before, after, expected in cases:
            with self.subTest(before=before, after=after):
                self.assertIs(release.needs_version_change(self.distributed, before, after), expected)

    def test_non_distributed_changes_do_not_require_a_version_change(self) -> None:
        self.assertFalse(release.needs_version_change(["README.md", "docs/changelog.md"], "1.2.3", "1.2.3"))
        self.assertFalse(release.needs_version_change([], "not-semver", "still-not-semver"))

    def test_prerelease_precedence_matches_semver(self) -> None:
        ordered = [
            "1.0.0-alpha",
            "1.0.0-alpha.1",
            "1.0.0-alpha.beta",
            "1.0.0-beta",
            "1.0.0-beta.2",
            "1.0.0-beta.11",
            "1.0.0-rc.1",
            "1.0.0",
        ]
        for before, after in zip(ordered, ordered[1:]):
            with self.subTest(before=before, after=after):
                self.assertLess(release.compare_semver(before, after), 0)
                self.assertGreater(release.compare_semver(after, before), 0)

    def test_numeric_identifiers_are_compared_numerically_without_integer_conversion(self) -> None:
        huge = "9" * 5000
        self.assertLess(release.compare_semver("1.0.0-2", "1.0.0-11"), 0)
        self.assertLess(release.compare_semver(f"1.0.0-{huge}", f"1.0.0-1{huge}"), 0)
        self.assertGreater(release.compare_semver(f"{huge}.0.0", "999.0.0"), 0)

    def test_build_metadata_does_not_affect_precedence(self) -> None:
        self.assertEqual(release.compare_semver("1.2.3-alpha+one", "1.2.3-alpha+two"), 0)

    def test_invalid_semver_is_rejected(self) -> None:
        invalid = [
            "1.0",
            "v1.0.0",
            "01.0.0",
            "1.01.0",
            "1.0.00",
            "1.0.0-",
            "1.0.0-alpha..1",
            "1.0.0-01",
            "1.0.0+",
            "1.0.0+build..1",
            "1.0.0-β",
            100,
            None,
        ]
        for version in invalid:
            with self.subTest(version=version):
                with self.assertRaisesRegex(ValueError, "Invalid semantic version"):
                    release.parse_semver(version)


if __name__ == "__main__":
    unittest.main()
