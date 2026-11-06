#!/usr/bin/env python3
"""Keep signed existing availability while refusing all untested promotions."""
import contextlib
import importlib.util
import io
import pathlib
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import test_canary_gate as fixtures

ROOT = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("retention_index", ROOT / "scripts/index_repo.py")
indexer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(indexer)


class RetainedStable(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.keys = tempfile.TemporaryDirectory()
        cls.home = pathlib.Path(cls.keys.name)
        cls.home.chmod(0o700)
        subprocess.run(["gpg", "--homedir", str(cls.home), "--batch", "--pinentry-mode", "loopback",
                        "--passphrase", "", "--quick-gen-key", "Retention fixture <fixture@example.invalid>",
                        "ed25519", "sign", "1d"], check=True, capture_output=True)
        cls.public = cls.home / "fixture.gpg"
        cls.public.write_bytes(subprocess.run(["gpg", "--homedir", str(cls.home), "--export"],
                                             check=True, capture_output=True).stdout)

    @classmethod
    def tearDownClass(cls):
        subprocess.run(["gpgconf", "--homedir", str(cls.home), "--kill", "gpg-agent"],
                       check=False, capture_output=True)
        cls.keys.cleanup()

    def setUp(self):
        self.f = fixtures.CanaryGate(methodName="runTest")
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        import canary_gate
        canary_gate.promote(self.f.deb, self.f.destination, self.f.candidate, self.f.trust)
        result, text = self.f.index()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Package: seeds", text)
        self.sign()
        self.f.policy = {"contract": canary_gate.CONTRACT, "keys": {}, "canaries": {},
                         "retain_published_seeds": True}
        self.f.save_policy()

    def sign(self):
        release = self.f.root / "dists/stable/Release"
        subprocess.run(["gpg", "--homedir", str(self.home), "--batch", "--yes", "--clearsign",
                        "--output", str(release.with_name("InRelease")), str(release)],
                       check=True, capture_output=True)

    def run_index(self, keyring=None):
        with mock.patch.object(indexer, "DEFAULT_PUBLISHED_KEY", keyring or self.public, create=True), \
                mock.patch.object(sys, "argv", ["index", "--root", str(self.f.root),
                                               "--channel", "stable", "--canary-trust", str(self.f.trust)]), \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return indexer.main()

    def published(self):
        return {str(p.relative_to(self.f.root)): p.read_bytes()
                for p in (self.f.root / "dists").rglob("*") if p.is_file()}

    def test_empty_enrollment_retains_signed_existing_seeds_but_excludes_new_071(self):
        self.f.package("seeds", "0.7.1", "stable")
        self.f.package("desire-path", "0.1.0", "stable")
        self.assertEqual(self.run_index(), 3)
        text = (self.f.root / "dists/stable/main/binary-amd64/Packages").read_text()
        self.assertIn("Package: seeds\nVersion: 0.7.2", text)
        self.assertNotIn("Version: 0.7.1", text)
        self.assertIn("Package: desire-path", text)

    def test_same_filename_changed_bytes_aborts_without_rewriting_indexes(self):
        before = self.published()
        self.f.destination.write_bytes(self.f.destination.read_bytes() + b"changed")
        self.assertEqual(self.run_index(), 2)
        self.assertEqual(self.published(), before)

    def test_tampered_signed_release_aborts_without_rewriting_indexes(self):
        path = self.f.root / "dists/stable/InRelease"
        path.write_bytes(path.read_bytes().replace(b"Suite: stable", b"Suite: altered"))
        before = self.published()
        self.assertEqual(self.run_index(), 2)
        self.assertEqual(self.published(), before)

    def test_packages_must_match_the_signed_release(self):
        path = self.f.root / "dists/stable/main/binary-amd64/Packages"
        path.write_bytes(path.read_bytes() + b"tampered\n")
        before = self.published()
        self.assertEqual(self.run_index(), 2)
        self.assertEqual(self.published(), before)

    def test_missing_signed_baseline_cannot_authorize_retention(self):
        (self.f.root / "dists/stable/InRelease").unlink()
        before = self.published()
        self.assertEqual(self.run_index(), 2)
        self.assertEqual(self.published(), before)

    def test_wrong_repository_key_cannot_authorize_retention(self):
        before = self.published()
        self.assertEqual(self.run_index(ROOT / "key.gpg"), 2)
        self.assertEqual(self.published(), before)

    def test_missing_old_pool_bytes_cannot_silently_remove_the_package(self):
        self.f.destination.unlink()
        before = self.published()
        self.assertEqual(self.run_index(), 2)
        self.assertEqual(self.published(), before)

    def test_enrollment_requires_explicitly_ending_retention(self):
        self.f.policy["keys"] = {"enrolled": {"public_key_sha256": "a" * 64}}
        self.f.save_policy()
        before = self.published()
        self.assertEqual(self.run_index(), 2)
        self.assertEqual(self.published(), before)

    def test_retention_is_never_promotion_authority(self):
        import canary_gate
        destination = self.f.root / "other-stable" / self.f.deb.name
        with self.assertRaises(canary_gate.Refused):
            canary_gate.promote(self.f.deb, destination, self.f.candidate, self.f.trust)
        self.assertFalse(destination.exists())

    def test_ending_retention_does_not_reactivate_it_on_empty_or_revoked_keys(self):
        self.f.policy["retain_published_seeds"] = False
        self.f.save_policy()
        self.assertEqual(self.run_index(), 3)
        text = (self.f.root / "dists/stable/main/binary-amd64/Packages").read_text()
        self.assertNotIn("Package: seeds", text)

    def test_expired_signed_baseline_aborts_without_rewriting_indexes(self):
        release = self.f.root / "dists/stable/Release"
        text = release.read_text()
        text = "\n".join("Valid-Until: Thu, 01 Jan 1970 00:00:00 GMT" if l.startswith("Valid-Until:")
                         else l for l in text.splitlines()) + "\n"
        release.write_text(text)
        self.sign()
        before = self.published()
        self.assertEqual(self.run_index(), 2)
        self.assertEqual(self.published(), before)


if __name__ == "__main__":
    unittest.main(verbosity=2)
