#!/usr/bin/env python3
"""The transitional package is built only when the pool can satisfy its Depends.

Review finding (PR 5): when every tambo build refused, the builder still emitted
caboodle 0.3.4-1 Depends tambo, and the publisher signs a partial pool.
"""
import importlib.util
import json
import pathlib
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
spec = importlib.util.spec_from_file_location("build_debs", ROOT / "scripts/build_debs.py")
build_debs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build_debs)
TOOL = {"package": "tambo", "transitional": {"package": "caboodle", "version": "0.3.4-1",
                                             "depends": "tambo (>= 0.3.4)"}}


def fake_deb(pool, name, version, arch):
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        (root / "DEBIAN").mkdir()
        (root / "DEBIAN/control").write_text(
            f"Package: {name}\nVersion: {version}\nArchitecture: {arch}\n"
            "Maintainer: test <t@example.invalid>\nDescription: fixture\n")
        out = pool / name / f"{name}_{version}_{arch}.deb"
        out.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["dpkg-deb", "--root-owner-group", "--build", str(root), str(out)],
                       check=True, stdout=subprocess.DEVNULL)


class TransitionalGate(unittest.TestCase):
    def run_gate(self, debs):
        with tempfile.TemporaryDirectory() as tmp:
            pool = pathlib.Path(tmp)
            for name, version, arch in debs:
                fake_deb(pool, name, version, arch)
            try:
                built = build_debs.build_transitional("tambo", TOOL, "amd64", pool)
                return "built" if built else "exists"
            except build_debs.Refused:
                self.assertFalse((pool / "caboodle").exists(), "refusal must write nothing")
                return "refused"

    def test_satisfying_target_builds(self):
        self.assertEqual(self.run_gate([("tambo", "0.3.4", "amd64")]), "built")

    def test_newer_target_builds(self):
        self.assertEqual(self.run_gate([("tambo", "0.3.5", "amd64")]), "built")

    def test_empty_pool_refuses(self):
        self.assertEqual(self.run_gate([]), "refused")

    def test_too_old_target_refuses(self):
        self.assertEqual(self.run_gate([("tambo", "0.3.3", "amd64")]), "refused")

    def test_other_arch_only_refuses(self):
        self.assertEqual(self.run_gate([("tambo", "0.3.4", "arm64")]), "refused")

    def test_wrong_package_name_refuses(self):
        self.assertEqual(self.run_gate([("tambox", "0.3.4", "amd64")]), "refused")


class ChannelWithoutReleases(unittest.TestCase):
    def test_no_release_in_channel_skips_transition_cleanly(self):
        # publish.yml runs every channel; tambo has no nightly releases. That must
        # neither build nor REFUSE the transitional (a refusal fails every run).
        with tempfile.TemporaryDirectory() as tmp:
            manifest = pathlib.Path(tmp) / "tools.json"
            manifest.write_text(json.dumps({"tambo": dict(TOOL, tag_prefix="v", arches=["amd64"])}))
            saved = build_debs.releases, sys.argv
            build_debs.releases = lambda repo, prefix, channel: []
            sys.argv = ["build_debs", "--manifest", str(manifest), "--pool", tmp, "--channel", "nightly"]
            try:
                self.assertEqual(build_debs.main(), 0)
            finally:
                build_debs.releases, sys.argv = saved
            self.assertEqual(list(pathlib.Path(tmp).rglob("*.deb")), [])


if __name__ == "__main__":
    sys.exit(unittest.main())
