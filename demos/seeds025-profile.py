#!/usr/bin/env python3
"""Replay the public 0.25 receipt admission in a private pool; never install."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import canary_gate

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--deb', type=Path, required=True)
args = parser.parse_args()
digest = '6c97a5ea5ade4d4b2e8ddd81b0d6e366b18b7a128046b8e8973ad1ab7c6d0c3d'
assert hashlib.sha256(args.deb.read_bytes()).hexdigest() == digest
receipt = json.loads((ROOT / 'canary/receipts' / (digest + '.json')).read_bytes())
before = subprocess.run(
    ['git', 'show', '32953fe7e3629af0a163cc9e7071606bc0a0b56d:canary/policy.json'],
    cwd=ROOT, check=True, capture_output=True).stdout
with tempfile.TemporaryDirectory(prefix='public-receipt-demo-') as temporary:
    root = Path(temporary)
    trust = root / 'canary'
    shutil.copytree(ROOT / 'canary', trust)
    destination = root / 'stable' / args.deb.name
    (trust / 'policy.json').write_bytes(before)
    try:
        canary_gate.promote(args.deb, destination, receipt['candidate'], trust)
    except canary_gate.Refused as error:
        assert str(error) == 'canary-policy-mismatch', str(error)
        assert not destination.exists()
        print('PRIVATE FIXTURE before: REFUSED canary-policy-mismatch; no stable copy')
    else:
        raise AssertionError('old policy admitted the new profile')
    shutil.copyfile(ROOT / 'canary/policy.json', trust / 'policy.json')
    canary_gate.promote(args.deb, destination, receipt['candidate'], trust)
    assert hashlib.sha256(destination.read_bytes()).hexdigest() == digest
    print('PRIVATE FIXTURE after: PASS; byte-identical 0.25.0-1 amd64 copy')
