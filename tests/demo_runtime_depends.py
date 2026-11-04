#!/usr/bin/env python3
"""Reproduce packaging metadata before/after on the same native ELF fixture."""
import hashlib
import importlib.util
import io
import pathlib
import platform
import subprocess
import tarfile
import tempfile
from unittest.mock import patch

BASE = 'cc6d711c955091d38e8eee6a9709829958017e7a'
ROOT = pathlib.Path(__file__).resolve().parents[1]
arch = {'x86_64': 'amd64', 'aarch64': 'arm64'}[platform.machine()]
with tempfile.TemporaryDirectory() as directory:
    tmp = pathlib.Path(directory)
    base = tmp / 'base.py'
    base.write_bytes(subprocess.run(['git', 'show', BASE + ':scripts/build_debs.py'], cwd=ROOT,
                                   check=True, capture_output=True).stdout)
    source = tmp / 'fixture.c'
    source.write_text('#include <stdio.h>\nint main(void) { return puts("fixture") < 0; }\n')
    subprocess.run(['cc', str(source), '-o', str(tmp / 'fixture')], check=True)
    binary = (tmp / 'fixture').read_bytes()
    archive = io.BytesIO()
    with tarfile.open(fileobj=archive, mode='w:gz') as tar:
        member = tarfile.TarInfo('fixture')
        member.size = len(binary)
        tar.addfile(member, io.BytesIO(binary))
    data = archive.getvalue()
    tool = dict(package='fixture', bins=['fixture'], tag_prefix='v', asset='fixture.tar.gz',
                description='native demo fixture', debian_revision='1')
    rel = dict(tag_name='v1.0', assets=[dict(name='fixture.tar.gz', browser_download_url='https://example.invalid/fixture')])
    print('Same locally compiled ELF, verified synthetic archive; no release executable run.', flush=True)
    for label, path in [('BASE ' + BASE[:8], base), ('CANDIDATE working source', ROOT / 'scripts/build_debs.py')]:
        spec = importlib.util.spec_from_file_location('demo_builder', path)
        builder = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(builder)
        with patch.object(builder, 'fetch', return_value=data), patch.object(builder, 'expected_sha', return_value=hashlib.sha256(data).hexdigest()):
            deb = builder.build('fixture', tool, rel, arch, tmp / label.split()[0])
        depends = subprocess.run(['dpkg-deb', '-f', str(deb), 'Depends'], check=True, capture_output=True, text=True).stdout.strip()
        extracted = tmp / ('extracted-' + label.split()[0])
        subprocess.run(['dpkg-deb', '-x', str(deb), str(extracted)], check=True)
        assert (extracted / 'usr/bin/fixture').read_bytes() == binary
        print(label + ': Depends=' + (depends or 'ABSENT') + '; executable bytes unchanged', flush=True)
