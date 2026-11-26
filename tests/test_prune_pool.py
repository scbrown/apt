#!/usr/bin/env python3
"""Served-pool retention keeps newest versions, pins and indexed stable Seeds."""
import json
import pathlib
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/prune_pool.py"


class PrunePool(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name)

    def deb(self, channel, pkg, ver, arch="amd64"):
        path = self.root / "pool" / channel / pkg / f"{pkg}_{ver}_{arch}.deb"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x" * 10)
        return path

    def run_prune(self, keep=2, protect=None, dry=False):
        policy = self.root / "retention.json"
        policy.write_text(json.dumps({"keep": keep, "protect": protect or {}}))
        argv = [sys.executable, str(SCRIPT), "--root", str(self.root), "--retention", str(policy)]
        return subprocess.run(argv + (["--dry-run"] if dry else []), capture_output=True, text=True)

    def test_keeps_newest_in_debian_order_across_arches(self):
        # 0.10.0 > 0.9.0 and 0.2.0-2 > 0.2.0-1: string order would get both wrong.
        for v in ("0.9.0", "0.10.0", "0.2.0-1", "0.2.0-2"):
            self.deb("stable", "tool", v, "amd64")
            self.deb("stable", "tool", v, "arm64")
        r = self.run_prune(keep=2)
        self.assertEqual(r.returncode, 0, r.stderr)
        left = sorted(p.name for p in (self.root / "pool/stable/tool").iterdir())
        self.assertEqual(left, ["tool_0.10.0_amd64.deb", "tool_0.10.0_arm64.deb",
                                "tool_0.9.0_amd64.deb", "tool_0.9.0_arm64.deb"])

    def test_protected_rollback_target_survives(self):
        for v in ("0.27.0-1", "0.28.0-1", "0.28.3-1", "0.29.0-1"):
            self.deb("canary", "seeds", v)
        r = self.run_prune(keep=2, protect={"canary/seeds": [{"version": "0.27.0-1"}]})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue((self.root / "pool/canary/seeds/seeds_0.27.0-1_amd64.deb").exists())
        self.assertFalse((self.root / "pool/canary/seeds/seeds_0.28.0-1_amd64.deb").exists())

    def test_indexed_stable_seeds_survive(self):
        old = self.deb("stable", "seeds", "0.19.2-1")
        for v in ("0.20.0-1", "0.21.0-1", "0.22.0-1"):
            self.deb("stable", "seeds", v)
        d = self.root / "dists/stable/main/binary-amd64"
        d.mkdir(parents=True)
        (d / "Packages").write_text("Package: seeds\nVersion: 0.19.2-1\n"
                                    "Filename: pool/stable/seeds/seeds_0.19.2-1_amd64.deb\n\n")
        r = self.run_prune(keep=2)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(old.exists())
        self.assertFalse((self.root / "pool/stable/seeds/seeds_0.20.0-1_amd64.deb").exists())

    def test_dry_run_removes_nothing(self):
        paths = [self.deb("stable", "tool", v) for v in ("1.0.0", "1.1.0", "1.2.0")]
        r = self.run_prune(keep=2, dry=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("would remove pool/stable/tool/tool_1.0.0_amd64.deb", r.stdout)
        self.assertTrue(all(p.exists() for p in paths))

    def test_refuses_unknown_file_and_tiny_keep(self):
        self.deb("stable", "tool", "1.0.0")
        (self.root / "pool/stable/tool/notes.deb").write_text("?")
        self.assertNotEqual(self.run_prune(keep=2).returncode, 0)
        (self.root / "pool/stable/tool/notes.deb").unlink()
        self.assertNotEqual(self.run_prune(keep=1).returncode, 0)
        self.assertTrue((self.root / "pool/stable/tool/tool_1.0.0_amd64.deb").exists())


if __name__ == "__main__":
    unittest.main()
