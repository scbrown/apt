#!/usr/bin/env python3
"""Prepare a real signed private canary index without stable admission or installs."""
import contextlib
import importlib.util
import io
import json
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import test_retained_stable as retention

ROOT = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('prepare_fixture', ROOT / 'tests/prepare-lifecycle-fixture.py')
prepare = importlib.util.module_from_spec(spec)
spec.loader.exec_module(prepare)


class PrivateCanaryFixture(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        retention.RetainedStable.setUpClass()

    @classmethod
    def tearDownClass(cls):
        retention.RetainedStable.tearDownClass()

    def setUp(self):
        self.published = retention.RetainedStable(methodName='runTest')
        self.published.setUp()
        self.addCleanup(self.published.doCleanups)
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = pathlib.Path(self.temporary.name)
        self.source = self.root / 'source'
        (self.source / 'scripts').mkdir(parents=True)
        for name in ('index_repo.py', 'canary_gate.py'):
            shutil.copyfile(ROOT / 'scripts' / name, self.source / 'scripts' / name)
        shutil.copyfile(retention.RetainedStable.public, self.source / 'key.gpg')
        keys = subprocess.run(['gpg', '--homedir', str(retention.RetainedStable.home),
                               '--batch', '--with-colons', '--list-keys'],
                              check=True, capture_output=True, text=True).stdout
        fingerprint = next(row.split(':')[9] for row in keys.splitlines() if row.startswith('fpr:'))
        (self.source / 'FINGERPRINT').write_text(fingerprint)
        (self.source / 'tools.json').write_text(json.dumps({
            'seeds': {'package': 'seeds', 'bins': ['sd'], 'aliases': {}}}))
        (self.source / 'canary').mkdir()
        (self.source / 'canary/policy.json').write_text(json.dumps({
            'contract': retention.fixtures.gate.CONTRACT, 'keys': {}, 'canaries': {},
            'retain_published_seeds': True}))
        self.pool = self.root / 'candidates'
        self.version = '0.7.2-1+lifecycle123'
        candidate = self.published.f.package('seeds', self.version, 'canary')
        self.candidate = self.pool / 'canary/seeds' / candidate.name
        self.candidate.parent.mkdir(parents=True)
        shutil.copyfile(candidate, self.candidate)
        self.site = self.root / 'private-site'

    def fetch(self, url):
        self.assertTrue(url.startswith(prepare.URL + '/'))
        relative = url.removeprefix(prepare.URL + '/')
        return (self.published.f.root / relative).read_bytes()

    def run_prepare(self):
        with mock.patch.object(prepare, 'ROOT', self.source), \
                mock.patch.object(prepare, 'fetch', side_effect=self.fetch), \
                mock.patch.object(sys, 'argv', ['prepare', '--pool', str(self.pool),
                                               '--site', str(self.site)]), \
                contextlib.redirect_stdout(io.StringIO()):
            prepare.main()

    def test_canary_candidate_and_signed_baseline_create_only_private_canary(self):
        self.run_prepare()
        pairs = json.loads((self.site / 'lifecycle-pairs.json').read_text())
        self.assertEqual([(p['package'], p['old'], p['new']) for p in pairs],
                         [('seeds', '0.7.2', self.version)])
        packages = (self.site / 'dists/canary/main/binary-amd64/Packages').read_text()
        self.assertIn('Version: 0.7.2\n', packages)
        self.assertIn('Version: ' + self.version + '\n', packages)
        self.assertIn('Filename: pool/canary/seeds/', packages)
        self.assertFalse((self.site / 'dists/stable').exists())
        self.assertFalse((self.site / '.throwaway-key').exists())
        subprocess.run(['gpgv', '--homedir', str(self.root), '--keyring',
                        str(self.site / 'fixture.gpg'), self.site / 'dists/canary/InRelease'],
                       check=True, capture_output=True)

    def test_changed_published_package_refuses_before_indexing(self):
        self.published.f.destination.write_bytes(self.published.f.destination.read_bytes() + b'changed')
        with self.assertRaisesRegex(prepare.Refused, 'published DEB hash/size mismatch'):
            self.run_prepare()
        self.assertFalse((self.site / 'dists').exists())

    def test_stable_seed_candidate_is_not_a_canary_input(self):
        stable = self.pool / 'stable/seeds' / self.candidate.name
        stable.parent.mkdir(parents=True)
        self.candidate.rename(stable)
        with self.assertRaisesRegex(prepare.Refused, 'must come from canary'):
            self.run_prepare()
        self.assertFalse((self.site / 'dists').exists())

    def test_reverting_to_stable_suite_cannot_bypass_production_admission(self):
        with mock.patch.object(prepare, 'FIXTURE_SUITE', 'stable'):
            with self.assertRaises(subprocess.CalledProcessError):
                self.run_prepare()
        self.assertFalse((self.site / 'dists/stable/InRelease').exists())


if __name__ == '__main__':
    unittest.main()
