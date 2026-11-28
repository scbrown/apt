#!/usr/bin/env python3
"""The publish gate fails on any refusal except seeds-promotion-pending (stable seeds with no receipt).

Refusal lines below are copied from a real failing publish run's log (timestamps stripped).
"""
import json
import pathlib
import subprocess
import sys
import tempfile
import unittest

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
GATE = ROOT / "scripts/publish_gate.py"

BUILD_PENDING = "\n".join(
    f"REFUSED seeds seeds-ai-v{v} {a}: unavailable-canary-evidence"
    for v in ("0.31.1", "0.31.0", "0.30.2") for a in ("amd64", "arm64")) + "\n"
INDEX_PENDING = "".join(
    f"REFUSED stable seeds_{v}_{a}.deb: unavailable-canary-evidence\n"
    for v in ("0.14.0-1", "0.15.0-1", "0.16.0-1") for a in ("amd64", "arm64"))
OK = "built yupana v0.20.0 amd64\nexists bobbin v0.38.0 amd64\n"


class PublishGate(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name)
        self.gate = self.root / "gate"
        self.gate.mkdir()
        self.receipts = self.root / "receipts"
        self.receipts.mkdir()
        self.policy = self.root / "policy.json"
        self.policy.write_text(json.dumps({"contract": "seeds-canary-v1", "keys": {"k": {}}, "canaries": {"c": {}}}))
        # the real shape: only the stable channel has refusals
        for channel in ("canary", "stable", "nightly"):
            self.log("build", channel, OK, 0)
            self.log("index", channel, f"{channel}: 12 packages, Release written\n", 0)

    def log(self, kind, channel, text, rc):
        (self.gate / f"{kind}-{channel}.log").write_text(text)
        (self.gate / f"{kind}-{channel}.rc").write_text(f"{rc}\n")

    def receipt(self, tag, version):
        (self.receipts / f"{tag}.json").write_text(json.dumps({"candidate": {"tag": tag, "version": version}}))

    def run_gate(self):
        r = subprocess.run([sys.executable, str(GATE), "--gate-dir", str(self.gate), "--policy", str(self.policy),
                            "--receipts", str(self.receipts)], capture_output=True, text=True)
        return r.returncode, r.stdout + r.stderr

    def pending_state(self):
        self.log("build", "stable", OK + BUILD_PENDING, 1)
        self.log("index", "stable", INDEX_PENDING + "stable: 9 packages, Release written\n", 3)

    def test_current_state_is_a_warning_not_a_failure(self):
        self.receipt("seeds-ai-v0.19.2", "0.19.2-1")  # the one real receipt: names none of the refused ones
        self.pending_state()
        code, out = self.run_gate()
        self.assertEqual(code, 0, out)
        self.assertIn("::warning title=seeds promotion pending::", out)
        self.assertIn("seeds-ai-v0.31.1", out)

    def test_clean_run_passes_quietly(self):
        code, out = self.run_gate()
        self.assertEqual(code, 0, out)
        self.assertNotIn("warning", out)

    def test_a_different_refused_package_still_fails(self):
        self.pending_state()
        self.log("build", "stable", OK + BUILD_PENDING +
                 "REFUSED yupana v0.20.0 amd64: yupana: sha256 aa != published bb\n", 1)
        code, out = self.run_gate()
        self.assertEqual(code, 1)
        self.assertIn("unexpected: REFUSED yupana", out)

    def test_unknown_still_fails(self):
        self.log("build", "stable", OK + BUILD_PENDING + "UNKNOWN bobbin: HTTP Error 502\n", 1)
        self.assertEqual(self.run_gate()[0], 1)

    def test_seeds_refused_for_another_reason_fails(self):
        self.log("build", "stable", "REFUSED seeds seeds-ai-v0.31.1 amd64: canary-architecture-mismatch\n", 1)
        self.assertEqual(self.run_gate()[0], 1)

    def test_a_receipt_that_exists_but_still_refuses_is_not_pending(self):
        self.pending_state()
        self.receipt("seeds-ai-v0.31.1", "0.31.1-1")  # expired or invalid receipt is a real failure
        code, out = self.run_gate()
        self.assertEqual(code, 1)
        self.assertIn("a receipt names seeds-ai-v0.31.1", out)

    def test_index_refusal_of_a_version_with_a_receipt_fails(self):
        self.pending_state()
        self.receipt("seeds-ai-v0.15.0", "0.15.0-1")
        code, out = self.run_gate()
        self.assertEqual(code, 1)
        self.assertIn("a receipt names 0.15.0-1", out)

    def test_expected_refusal_outside_stable_fails(self):
        self.log("build", "canary", BUILD_PENDING, 1)
        self.assertEqual(self.run_gate()[0], 1)

    def test_nonzero_exit_without_a_recorded_refusal_fails(self):
        self.log("build", "stable", OK, 1)
        code, out = self.run_gate()
        self.assertEqual(code, 1)
        self.assertIn("cause unknown", out)

    def test_traceback_fails_even_beside_expected_lines(self):
        self.log("build", "stable", BUILD_PENDING + "Traceback (most recent call last):\n  boom\n", 1)
        self.assertEqual(self.run_gate()[0], 1)

    def test_index_outcome_other_than_0_or_3_fails(self):
        self.log("index", "stable", INDEX_PENDING, 2)
        self.assertEqual(self.run_gate()[0], 1)

    def test_exit_zero_with_refusal_lines_fails(self):
        self.log("build", "stable", BUILD_PENDING, 0)
        self.assertEqual(self.run_gate()[0], 1)

    def test_missing_log_or_unreadable_policy_cannot_be_judged(self):
        (self.gate / "index-nightly.log").unlink()
        self.assertEqual(self.run_gate()[0], 1)
        self.log("index", "nightly", "ok\n", 0)
        self.policy.write_text("not json")
        self.assertEqual(self.run_gate()[0], 1)

    def test_shipped_policy_and_receipts_are_readable(self):
        # the shipped policy and receipts parse and the gate accepts a clean run with them
        code = subprocess.run([sys.executable, str(GATE), "--gate-dir", str(self.gate),
                               "--policy", str(ROOT / "canary/policy.json"),
                               "--receipts", str(ROOT / "canary/receipts")], capture_output=True, text=True).returncode
        self.assertEqual(code, 0)

    def test_workflow_captures_logs_and_calls_the_gate(self):
        wf = yaml.safe_load((ROOT / ".github/workflows/publish.yml").read_text())
        steps = {s["name"]: s for s in wf["jobs"]["publish"]["steps"] if "name" in s}
        build = steps["Build packages for new releases"]["run"]
        self.assertIn('> "$gate/build-$channel.log"', build)
        self.assertIn('echo "$rc" > "$gate/build-$channel.rc"', build)
        self.assertIn('echo "GATE_DIR=$gate" >> "$GITHUB_ENV"', build)
        index = steps["Index and sign"]["run"]
        self.assertIn('> "$GATE_DIR/index-$channel.log"', index)
        self.assertIn('echo "$index_status" > "$GATE_DIR/index-$channel.rc"', index)
        final = steps["Fail on any refused or unknown release"]
        self.assertEqual(final["if"], "env.BUILD_STATUS != '0'")
        self.assertIn("scripts/publish_gate.py", final["run"])
        self.assertNotIn("exit 1", final["run"])


if __name__ == "__main__":
    unittest.main()
