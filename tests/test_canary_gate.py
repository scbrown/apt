#!/usr/bin/env python3
"""Native deb + Ed25519 controls. No host installs, network or board writes."""
import datetime as dt
import json
import pathlib
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import canary_gate as gate
import build_debs as builder


class CanaryGate(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = pathlib.Path(self.temporary.name)
        self.trust = self.root / "trust"
        (self.trust / "keys").mkdir(parents=True)
        (self.trust / "receipts").mkdir()
        self.private = self.root / "fixture-private.pem"
        subprocess.run(["openssl", "genpkey", "-algorithm", "ED25519", "-out", str(self.private)],
                       check=True, capture_output=True)
        public = self.trust / "keys/fixture.pem"
        subprocess.run(["openssl", "pkey", "-in", str(self.private), "-pubout", "-out", str(public)],
                       check=True, capture_output=True)
        self.policy = {"contract": gate.CONTRACT,
                       "keys": {"fixture": {"public_key_sha256": gate.sha256(public)}},
                       "canaries": {"fixture-canary": {"key_id": "fixture", "architecture": "amd64",
                                   "topology_sha256": "a" * 64, "st_version": "fixture-st-1"}}}
        self.save_policy()
        self.deb = self.package("seeds", "0.7.2", "canary")
        candidate = gate.package_identity(self.deb)
        candidate.update(repository="scbrown/seeds", tag="seeds-ai-v0.7.2", commit="b" * 40)
        self.candidate = candidate
        now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
        self.receipt = {"contract": gate.CONTRACT, "key_id": "fixture", "canary_id": "fixture-canary",
                        "topology_sha256": "a" * 64, "st_version": "fixture-st-1",
                        "measured_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                        "expires_at": (now + dt.timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                        "candidate": candidate, "checks": dict.fromkeys(gate.CHECKS, True),
                        "outcome": "PASS", "evidence_sha256": "c" * 64}
        self.destination = self.root / "pool/stable/seeds" / self.deb.name
        self.sign()

    def save_policy(self):
        (self.trust / "policy.json").write_text(json.dumps(self.policy))

    def package(self, name, version, channel):
        root = self.root / (name + version)
        (root / "DEBIAN").mkdir(parents=True)
        (root / "DEBIAN/control").write_text(
            f"Package: {name}\nVersion: {version}\nArchitecture: amd64\n"
            "Maintainer: Fixture <fixture@example.invalid>\nDescription: synthetic canary fixture\n"
            f" Upstream archive sha256: {'d' * 64}\n")
        deb = self.root / "pool" / channel / name / f"{name}_{version}_amd64.deb"
        deb.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["dpkg-deb", "--root-owner-group", "--build", str(root), str(deb)],
                       check=True, capture_output=True)
        return deb

    def sign(self, raw=None):
        stem = self.trust / "receipts" / self.candidate["deb_sha256"]
        data, signature = stem.with_suffix(".json"), stem.with_suffix(".sig")
        data.write_bytes(raw or (json.dumps(self.receipt, sort_keys=True) + "\n").encode())
        subprocess.run(["openssl", "pkeyutl", "-sign", "-inkey", str(self.private), "-rawin",
                        "-in", str(data), "-out", str(signature)], check=True, capture_output=True)

    def refuses(self):
        with self.assertRaises(gate.Refused):
            gate.promote(self.deb, self.destination, self.candidate, self.trust)
        self.assertFalse(self.destination.exists())

    def index(self):
        result = subprocess.run([sys.executable, str(ROOT / "scripts/index_repo.py"),
                                 "--root", str(self.root), "--channel", "stable",
                                 "--canary-trust", str(self.trust)], capture_output=True, text=True)
        packages = self.root / "dists/stable/main/binary-amd64/Packages"
        return result, packages.read_text() if packages.exists() else None

    def test_exact_signed_good_promotion_and_index(self):
        self.assertEqual(gate.promote(self.deb, self.destination, self.candidate, self.trust), self.destination)
        self.assertEqual(self.deb.read_bytes(), self.destination.read_bytes())
        self.assertIsNone(gate.promote(self.deb, self.destination, self.candidate, self.trust))
        result, text = self.index()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Package: seeds\nVersion: 0.7.2", text)

    def test_071_preexisting_pool_bypass_excluded_other_tool_retained(self):
        self.package("seeds", "0.7.1", "stable")
        self.package("desire-path", "0.1.0", "stable")
        result, text = self.index()
        self.assertEqual(result.returncode, 3, result.stderr)
        self.assertNotIn("Package: seeds", text)
        self.assertIn("Package: desire-path", text)
        print("DEMO unapproved seeds 0.7.1 -> excluded; unrelated tool -> retained")

    def test_missing_receipt(self):
        for path in (self.trust / "receipts").iterdir():
            path.unlink()
        self.refuses()

    def test_production_policy_has_no_test_trust(self):
        policy = json.loads((ROOT / "canary/policy.json").read_text())
        self.assertNotIn("fixture", policy["keys"])
        self.assertNotIn("fixture-canary", policy["canaries"])
        with self.assertRaises(gate.Refused):
            gate.verify(self.deb)

    def test_forged_signature(self):
        (self.trust / "receipts" / (self.candidate["deb_sha256"] + ".sig")).write_bytes(b"x" * 64)
        self.refuses()

    def test_tampered_receipt_bytes(self):
        path = self.trust / "receipts" / (self.candidate["deb_sha256"] + ".json")
        path.write_bytes(path.read_bytes() + b" ")
        self.refuses()

    def test_changed_public_key(self):
        (self.trust / "keys/fixture.pem").write_bytes(b"different")
        self.refuses()

    def test_wrong_contract_topology_host_arch_and_st(self):
        changes = [("contract", "old-contract"), ("topology_sha256", "e" * 64),
                   ("canary_id", "other"), ("st_version", "other")]
        for key, value in changes:
            with self.subTest(key=key):
                saved = self.receipt[key]
                self.receipt[key] = value
                self.sign()
                self.refuses()
                self.receipt[key] = saved
        self.policy["canaries"]["fixture-canary"]["architecture"] = "arm64"
        self.save_policy()
        self.sign()
        self.refuses()

    def test_each_smoke_check_and_failure_outcome(self):
        for check in gate.CHECKS:
            for value in (False, "true", 1, None):
                with self.subTest(check=check, value=value):
                    self.receipt["checks"][check] = value
                    self.sign()
                    self.refuses()
            self.receipt["checks"][check] = True
        for value in ("FAIL", "UNKNOWN", "ROLLBACK_FAILED"):
            self.receipt["outcome"] = value
            self.sign()
            self.refuses()

    def test_every_candidate_binding(self):
        for key in self.candidate:
            with self.subTest(key=key):
                self.receipt["candidate"] = dict(self.candidate, **{key: "wrong"})
                self.sign()
                self.refuses()

    def test_old_promotion_receipt_and_future_measurement(self):
        self.receipt["measured_at"] = "2020-01-01T00:00:00Z"
        self.receipt["expires_at"] = "2020-01-01T01:00:00Z"
        self.sign()
        self.refuses()
        # Durable stable approval still checks current topology/contract/signature.
        gate.verify(self.deb, self.trust)
        self.destination.parent.mkdir(parents=True)
        self.destination.write_bytes(self.deb.read_bytes())
        self.assertIsNone(gate.promote(self.deb, self.destination, self.candidate, self.trust))
        self.destination.unlink()
        self.receipt["measured_at"] = "2099-01-01T00:00:00Z"
        self.receipt["expires_at"] = "2099-01-01T01:00:00Z"
        self.sign()
        self.refuses()

    def test_duplicate_json_key_refused_even_signed(self):
        raw = json.dumps(self.receipt).replace('"outcome": "PASS"', '"outcome": "FAIL", "outcome": "PASS"')
        self.sign(raw.encode())
        self.refuses()

    def test_artifact_substitution_and_immutable_destination(self):
        self.destination.parent.mkdir(parents=True)
        self.destination.write_bytes(b"other")
        with self.assertRaises(gate.Refused):
            gate.promote(self.deb, self.destination, self.candidate, self.trust)
        self.assertEqual(self.destination.read_bytes(), b"other")

    def test_stable_builder_cannot_build_seeds_directly(self):
        tool = {"package": "seeds", "tag_prefix": "seeds-ai-v", "arches": ["amd64"],
                "asset": "fixture.tar.gz", "sums": "sidecar"}
        manifest = self.root / "tools.json"
        manifest.write_text(json.dumps({"seeds": tool}))
        # No canary -> no archive download/build path, even if a release exists.
        self.deb.unlink()
        with mock.patch.object(builder, "releases", return_value=[{"tag_name": "seeds-ai-v0.7.2"}]), \
                mock.patch.object(builder, "build", side_effect=AssertionError("direct build bypass")), \
                mock.patch.object(sys, "argv", ["build", "--manifest", str(manifest), "--pool",
                                               str(self.root / "pool"), "--channel", "stable"]):
            self.assertEqual(builder.main(), 1)
        self.assertFalse(self.destination.exists())

    def test_workflow_all_entrypoints_share_enforced_pipeline(self):
        workflow = (ROOT / ".github/workflows/publish.yml").read_text()
        self.assertEqual(workflow.count("for channel in canary stable nightly; do"), 2)
        self.assertIn('*) exit "$index_status"', workflow)
        self.assertNotIn("continue-on-error", workflow)
        ci = (ROOT / ".github/workflows/ci.yml").read_text()
        self.assertIn("python3 tests/test_canary_gate.py", ci)

    def test_builder_checks_commit_and_archive_before_promotion(self):
        tool = {"package": "seeds", "tag_prefix": "seeds-ai-v", "asset": "fixture.tar.gz", "sums": "sidecar"}
        release = {"tag_name": "seeds-ai-v0.7.2", "assets": []}
        with mock.patch.object(builder, "fetch", return_value=json.dumps({"sha": "b" * 40}).encode()), \
                mock.patch.object(builder, "expected_sha", return_value="d" * 64):
            builder.promote_seeds(tool, release, "amd64", self.root / "pool", self.trust)
        self.assertEqual(self.deb.read_bytes(), self.destination.read_bytes())
        self.destination.unlink()
        for sha, archive in (("e" * 40, "d" * 64), ("b" * 40, "e" * 64)):
            with self.subTest(commit=sha, archive=archive), \
                    mock.patch.object(builder, "fetch", return_value=json.dumps({"sha": sha}).encode()), \
                    mock.patch.object(builder, "expected_sha", return_value=archive):
                with self.assertRaises((gate.Refused, builder.Refused)):
                    builder.promote_seeds(tool, release, "amd64", self.root / "pool", self.trust)
                self.assertFalse(self.destination.exists())

    def test_malformed_policy_refuses(self):
        (self.trust / "policy.json").write_text('{"contract": "seeds-canary-v1", "keys": []}')
        self.refuses()


if __name__ == "__main__":
    unittest.main(verbosity=2)
