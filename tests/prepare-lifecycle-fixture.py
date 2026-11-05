#!/usr/bin/env python3
"""Prepare a private, throwaway-signed lifecycle repository; never install."""
import argparse
import functools
import hashlib
import io
import json
import pathlib
import shutil
import subprocess
import sys
import tarfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from build_debs import fetch, Refused

URL = 'https://scbrown.github.io/apt'
FIXTURE_SUITE = 'canary'


def run(*args, **kwargs):
    return subprocess.run(list(map(str, args)), check=True, **kwargs)


def fields(text):
    return dict(line.split(': ', 1) for line in text.splitlines()
                if not line.startswith(' ') and ': ' in line)


def version_cmp(a, b):
    if a['Version'] == b['Version']:
        return 0
    return -1 if subprocess.run(['dpkg', '--compare-versions', a['Version'], 'lt', b['Version']]).returncode == 0 else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pool', required=True, type=pathlib.Path)
    parser.add_argument('--site', required=True, type=pathlib.Path)
    args = parser.parse_args()
    site = args.site.resolve()
    site.mkdir(parents=True, exist_ok=False)
    keyhome = site / '.throwaway-key'
    keyhome.mkdir(mode=0o700)
    signed = site / 'upstream-InRelease'
    signed.write_bytes(fetch(URL + '/dists/stable/InRelease'))
    signature = run('gpgv', '--homedir', keyhome, '--keyring', ROOT / 'key.gpg',
                    '--status-fd', '1', signed, capture_output=True, text=True)
    fingerprint = (ROOT / 'FINGERPRINT').read_text().strip()
    if '[GNUPG:] VALIDSIG ' + fingerprint + ' ' not in signature.stdout:
        raise Refused('upstream InRelease signer did not match FINGERPRINT')
    (site / 'upstream-signature.log').write_text(signature.stdout + signature.stderr)
    hashes = {}
    active = False
    for line in signed.read_text().splitlines():
        if line == 'SHA256:':
            active = True
            continue
        if active and not line.startswith(' '):
            active = False
        if active:
            digest, size, name = line.split()
            hashes[name] = (digest, int(size))
    manifest = json.loads((ROOT / 'tools.json').read_text())
    tool_by_package = {t['package']: t for name, t in manifest.items() if not name.startswith('_')}
    old_rows = {}
    pairs = []
    if any(args.pool.glob('stable/seeds/*.deb')):
        raise Refused('Seeds lifecycle candidates must come from canary')
    candidates = [*args.pool.glob('stable/*/*.deb'), *args.pool.glob('canary/seeds/*.deb')]
    for candidate in sorted(candidates):
        info = fields(run('dpkg-deb', '-f', candidate, capture_output=True, text=True).stdout)
        package, arch = info['Package'], info['Architecture']
        # Transitional caboodle is covered by the separate transition fixture.
        if package not in tool_by_package:
            continue
        if arch not in old_rows:
            name = f'main/binary-{arch}/Packages'
            data = fetch(URL + '/dists/stable/' + name)
            if (hashlib.sha256(data).hexdigest(), len(data)) != hashes[name]:
                raise Refused(f'{arch}: signed Packages hash/size mismatch')
            (site / f'upstream-{arch}-Packages').write_bytes(data)
            old_rows[arch] = [fields(p) for p in data.decode().split('\n\n') if p.strip()]
        choices = [r for r in old_rows[arch] if r['Package'] == package]
        if not choices:
            raise Refused(f'{package}/{arch}: no published baseline')
        old = max(choices, key=functools.cmp_to_key(version_cmp))
        run('dpkg', '--compare-versions', info['Version'], 'gt', old['Version'])
        filename = pathlib.PurePosixPath(old['Filename'])
        if filename.is_absolute() or '..' in filename.parts or filename.parts[:2] != ('pool', 'stable'):
            raise Refused('unsafe published package filename')
        payload = fetch(URL + '/' + str(filename))
        if (hashlib.sha256(payload).hexdigest(), len(payload)) != (old['SHA256'], int(old['Size'])):
            raise Refused(f'{package}/{arch}: published DEB hash/size mismatch')
        destination = site / 'pool' / FIXTURE_SUITE / package / filename.name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(payload)
        newer = destination.parent / candidate.name
        shutil.copyfile(candidate, newer)
        for deb in (destination, newer):
            control = run('dpkg-deb', '--ctrl-tarfile', deb, capture_output=True).stdout
            with tarfile.open(fileobj=io.BytesIO(control)) as tar:
                if any(pathlib.PurePosixPath(m.name).name not in ('', 'control') for m in tar.getmembers()):
                    raise Refused(f'{package}: unexpected control file or maintainer script')
        tool = tool_by_package[package]
        aliases = list(tool.get('aliases', {}))
        if package == 'tambo':
            aliases.append('caboodle')
        pairs.append(dict(package=package, old=old['Version'], new=info['Version'],
                          arch=arch, command=tool['bins'][0], alias=aliases[0] if aliases else '',
                          old_sha256=old['SHA256'], new_sha256=hashlib.sha256(newer.read_bytes()).hexdigest()))
    if not pairs:
        raise Refused('no lifecycle pairs prepared')
    run(sys.executable, ROOT / 'scripts/index_repo.py', '--root', site, '--channel', FIXTURE_SUITE)
    try:
        run('gpg', '--homedir', keyhome, '--batch', '--pinentry-mode', 'loopback',
            '--passphrase', '', '--quick-gen-key', 'disposable lifecycle fixture', 'ed25519', 'sign', '1d',
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        exported = run('gpg', '--homedir', keyhome, '--batch', '--export', capture_output=True).stdout
        (site / 'fixture.gpg').write_bytes(exported)
        run('gpg', '--homedir', keyhome, '--batch', '--yes', '--clearsign',
            '--output', site / f'dists/{FIXTURE_SUITE}/InRelease', site / f'dists/{FIXTURE_SUITE}/Release')
    finally:
        try:
            run('gpgconf', '--homedir', keyhome, '--kill', 'gpg-agent')
        finally:
            shutil.rmtree(keyhome)
    (site / 'lifecycle-pairs.json').write_text(json.dumps(pairs, indent=2) + '\n')
    print(json.dumps({'prepared_pairs': len(pairs), 'installed': False}))


if __name__ == '__main__':
    main()
