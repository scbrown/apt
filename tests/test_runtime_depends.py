#!/usr/bin/env python3
"""Real ELF and .deb controls; no downloaded executable is run."""
import hashlib
import importlib.util
import io
import pathlib
import platform
import subprocess
import tarfile
import tempfile
import unittest
from unittest.mock import patch

ROOT = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('build_debs', ROOT / 'scripts/build_debs.py')
b = importlib.util.module_from_spec(spec)
spec.loader.exec_module(b)
ARCH = {'x86_64': 'amd64', 'aarch64': 'arm64'}[platform.machine()]


class RuntimeDepends(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name)
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        self.source = self.root / 'main.c'
        self.source.write_text('#include <stdio.h>\nint main(void) { return puts("fixture") < 0; }\n')
        self.compile(self.source, '-o', self.bin / 'fixture')

    def compile(self, *args):
        subprocess.run(['cc', *map(str, args)], check=True, capture_output=True)

    def test_native_libc_floor_and_wrong_arch(self):
        deps = b.runtime_depends(self.bin, ['fixture'], ARCH)
        self.assertRegex(deps, r'^libc6 \(>= 2\.[0-9]+(?:\.[0-9]+)?\)$')
        with self.assertRaisesRegex(b.Refused, 'architecture'):
            b.runtime_depends(self.bin, ['fixture'], 'arm64' if ARCH == 'amd64' else 'amd64')

    def test_static_elf_has_no_runtime_depends(self):
        source = self.root / 'static.c'
        source.write_text('void _start(void) { for (;;) {} }\n')
        self.compile('-nostdlib', '-static', source, '-o', self.bin / 'static')
        self.assertEqual(b.runtime_depends(self.bin, ['static'], ARCH), '')

    def library_binary(self, soname, version=None):
        lib = self.root / 'lib.c'
        lib.write_text('int fixture_api(void) { return 0; }\n')
        flags = []
        if version:
            script = self.root / 'versions'
            script.write_text(version + ' { global: fixture_api; };\n')
            flags = ['-Wl,--version-script=' + str(script)]
        self.compile('-shared', '-fPIC', lib, '-Wl,-soname,' + soname, *flags,
                     '-o', self.root / soname)
        self.source.write_text('extern int fixture_api(void); int main(void) { return fixture_api(); }\n')
        self.compile(self.source, '-L' + str(self.root), '-Wl,--no-as-needed',
                     '-l:' + soname, '-o', self.bin / 'fixture')

    def test_unknown_needed_library_refuses(self):
        self.library_binary('libunqualified.so.1')
        with self.assertRaisesRegex(b.Refused, 'unqualified runtime library libunqualified'):
            b.runtime_depends(self.bin, ['fixture'], ARCH)

    def test_future_cpp_abi_refuses(self):
        self.library_binary('libstdc++.so.6', 'GLIBCXX_99.0')
        with self.assertRaisesRegex(b.Refused, 'unqualified runtime ABI GLIBCXX_99.0'):
            b.runtime_depends(self.bin, ['fixture'], ARCH)

    def test_script_cannot_pass_as_static_elf(self):
        (self.bin / 'fixture').write_text('#!/bin/sh\nexit 0\n')
        with self.assertRaisesRegex(b.Refused, 'expected a Linux ELF'):
            b.runtime_depends(self.bin, ['fixture'], ARCH)

    def test_verified_archive_to_deb_and_immutable_existing_version(self):
        archive = io.BytesIO()
        binary = (self.bin / 'fixture').read_bytes()
        with tarfile.open(fileobj=archive, mode='w:gz') as tar:
            info = tarfile.TarInfo('release/fixture')
            info.size = len(binary)
            tar.addfile(info, io.BytesIO(binary))
            license_bytes = b'fixture release license\n'
            info = tarfile.TarInfo('release/LICENSE')
            info.size = len(license_bytes)
            tar.addfile(info, io.BytesIO(license_bytes))
        payload = archive.getvalue()
        tool = dict(package='fixture', bins=['fixture'], aliases={'alias': 'fixture'},
                    tag_prefix='v', asset='fixture.tar.gz', sums='sidecar',
                    description='fixture', debian_revision='1',
                    relations={'Breaks': 'old-fixture (<< 1)'})
        rel = dict(tag_name='v1.0', assets=[dict(name='fixture.tar.gz', browser_download_url='https://example.invalid/fixture')])
        out = self.root / 'pool'
        with patch.object(b, 'fetch', return_value=payload), patch.object(b, 'expected_sha', return_value=hashlib.sha256(payload).hexdigest()):
            deb = b.build('fixture', tool, rel, ARCH, out)
        fields = subprocess.run(['dpkg-deb', '-f', str(deb)], check=True, capture_output=True, text=True).stdout
        self.assertIn('Version: 1.0-1\n', fields)
        self.assertIn('Depends: libc6 (>= ', fields)
        self.assertIn('Breaks: old-fixture (<< 1)\n', fields)
        extracted = self.root / 'extracted'
        subprocess.run(['dpkg-deb', '-x', str(deb), str(extracted)], check=True)
        self.assertEqual((extracted / 'usr/bin/fixture').read_bytes(), binary)
        self.assertEqual((extracted / 'usr/share/doc/fixture/upstream-license').read_bytes(), license_bytes)
        self.assertIn(hashlib.sha256(payload).hexdigest(), (extracted / 'usr/share/doc/fixture/README.package').read_text())
        self.assertFalse((extracted / 'DEBIAN').exists())
        self.assertEqual((extracted / 'usr/bin/alias').readlink(), pathlib.Path('fixture'))
        before = deb.read_bytes()
        with patch.object(b, 'fetch', side_effect=AssertionError('must not fetch existing versions')):
            self.assertIsNone(b.build('fixture', tool, rel, ARCH, out))
        self.assertEqual(deb.read_bytes(), before)
        tool['debian_revision'] = '2'
        with patch.object(b, 'fetch', return_value=payload), patch.object(b, 'expected_sha', return_value='0' * 64):
            with self.assertRaisesRegex(b.Refused, 'sha256'):
                b.build('fixture', tool, rel, ARCH, out)
        self.assertFalse((out / f'fixture_1.0-2_{ARCH}.deb').exists())


if __name__ == '__main__':
    unittest.main()
