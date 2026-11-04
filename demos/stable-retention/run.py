#!/usr/bin/env python3
"""Repeatable source-only demo; real synthetic .debs and temporary signing keys."""
import hashlib
import pathlib
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tests'))
from test_retained_stable import RetainedStable

BASE = '00727f4372bf773e730610260b33ecfe58f9a98d'
print('Target: synthetic source fixtures; no install, network or production key', flush=True)
print('Before: original gate commit ' + BASE, flush=True)
for name in ('index_repo.py', 'canary_gate.py'):
    print('After source SHA256 ' + name + ': ' + hashlib.sha256((ROOT / 'scripts' / name).read_bytes()).hexdigest(), flush=True)
RetainedStable.setUpClass()
f = RetainedStable('runTest')
try:
    f.setUp()
    f.f.package('seeds', '0.7.1', 'stable')
    f.f.package('desire-path', '0.1.0', 'stable')
    baseline = f.published()
    old_policy = dict(f.f.policy)
    old_policy.pop('retain_published_seeds')
    import json
    (f.f.trust / 'policy.json').write_text(json.dumps(old_policy))
    with tempfile.TemporaryDirectory() as scratch:
        for name in ('index_repo.py', 'canary_gate.py'):
            data = subprocess.run(['git', 'show', BASE + ':scripts/' + name], cwd=ROOT,
                                  capture_output=True, check=True).stdout
            (pathlib.Path(scratch) / name).write_bytes(data)
        result = subprocess.run([sys.executable, str(pathlib.Path(scratch) / 'index_repo.py'),
                                 '--root', str(f.f.root), '--channel', 'stable',
                                 '--canary-trust', str(f.f.trust)], capture_output=True)
    index = f.f.root / 'dists/stable/main/binary-amd64/Packages'
    text = index.read_text()
    print('BEFORE empty enrollment: exit=%d; published Seeds 0.7.2=%s; new Seeds 0.7.1=%s; other tool=%s' %
          (result.returncode, 'Version: 0.7.2' in text, 'Version: 0.7.1' in text, 'Package: desire-path' in text), flush=True)
    assert result.returncode == 3 and 'Package: seeds' not in text and 'Package: desire-path' in text
    # Restore identical signed baseline; enable the new explicit pre-enrollment mode.
    for path, data in baseline.items():
        (f.f.root / path).write_bytes(data)
    f.f.save_policy()
    result = f.run_index()
    text = index.read_text()
    print('AFTER explicit retention: exit=%d; published Seeds 0.7.2=%s; new Seeds 0.7.1=%s; other tool=%s' %
          (result, 'Version: 0.7.2' in text, 'Version: 0.7.1' in text, 'Package: desire-path' in text), flush=True)
    assert result == 3 and 'Version: 0.7.2' in text and 'Version: 0.7.1' not in text and 'Package: desire-path' in text
    before = f.published()
    f.f.destination.write_bytes(f.f.destination.read_bytes() + b'changed')
    result = f.run_index()
    unchanged = f.published() == before
    print('CONTROL changed published bytes: exit=%d; all metadata unchanged=%s' % (result, unchanged), flush=True)
    assert result == 2 and unchanged
finally:
    f.doCleanups()
    RetainedStable.tearDownClass()
