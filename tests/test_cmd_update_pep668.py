"""Tests for the PEP 668 externally-managed-environment detector.

Covers the cmd_update retry-with-break-system-packages path: we want to
fire only on genuine PEP 668 stderr and never on unrelated pip failures
(auth, network, missing package, build errors).
"""
import unittest

from agentkthx.cli import _is_externally_managed_error


class TestIsExternallyManagedError(unittest.TestCase):
    """The detector must catch real PEP 668 stderr and reject lookalikes."""

    # --- positives: real PEP 668 phrasings -----------------------------------

    def test_canonical_marker(self):
        """Canonical phrase pip emits under PEP 668."""
        err = (
            "error: externally-managed-environment\n\n"
            "x This environment is externally managed\n"
        )
        self.assertTrue(_is_externally_managed_error(err))

    def test_spaced_variant(self):
        """Older phrasing before pip aligned on the hyphenated term."""
        err = "This environment is externally managed"
        self.assertTrue(_is_externally_managed_error(err))

    def test_hint_only_with_pep668_marker(self):
        """Some distros reword the main error but keep the hint verbatim."""
        err = (
            "Some custom distro error message\n"
            "hint: pass --break-system-packages to override. See PEP 668."
        )
        self.assertTrue(_is_externally_managed_error(err))

    def test_full_user_reported_error(self):
        """The exact error the user reported in the session that prompted this fix."""
        err = (
            "error: externally-managed-environment\n\n"
            "x This environment is externally managed\n"
            "note: If you believe this is a mistake, please contact your Python "
            "installation or OS distribution provider. You can override this, at "
            "the risk of breaking your Python installation or OS, by passing "
            "--break-system-packages.\n"
            "hint: See PEP 668 for the detailed specification."
        )
        self.assertTrue(_is_externally_managed_error(err))

    # --- negatives: unrelated pip failures -----------------------------------

    def test_empty_stderr(self):
        self.assertFalse(_is_externally_managed_error(""))

    def test_none_stderr(self):
        # _is_externally_managed_error guards against None defensively
        self.assertFalse(_is_externally_managed_error(None))  # type: ignore[arg-type]

    def test_missing_package(self):
        err = "ERROR: Could not find a version that satisfies the requirement agentkthx"
        self.assertFalse(_is_externally_managed_error(err))

    def test_network_error(self):
        err = "Connection error: HTTPSConnectionPool(host='pypi.org', port=443)"
        self.assertFalse(_is_externally_managed_error(err))

    def test_build_error(self):
        err = "error: invalid command 'bdist_wheel'"
        self.assertFalse(_is_externally_managed_error(err))

    def test_auth_error(self):
        err = "ERROR: Could not install requirement due to an authentication error"
        self.assertFalse(_is_externally_managed_error(err))

    def test_break_system_packages_without_pep668_marker(self):
        """A stderr that mentions --break-system-packages without PEP 668 context.

        Should NOT fire — the hint appears in other contexts too (e.g. custom
        pip wrappers, blog posts copy-pasted into error logs). We require
        either the canonical marker phrase or the PEP 668 hint pairing.
        """
        err = "Some other error. You could try --break-system-packages maybe."
        self.assertFalse(_is_externally_managed_error(err))


if __name__ == "__main__":
    unittest.main()
