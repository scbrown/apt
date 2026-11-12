#!/usr/bin/env python3
"""Offline synthetic release -> Debian package, using the selected builder."""
import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / 'scripts'))
def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
fixture = load('fixture', root / 'tests/test_python_package.py')
fixture.b = load('builder', Path(sys.argv[1]))
with tempfile.TemporaryDirectory() as tmp:
    try:
        deb = fixture.PythonPackage().build(Path(tmp), fixture.archive())
    except fixture.b.Refused as error:
        print('REFUSED:', error)
    else:
        print('BUILT:', deb.name)
        print('Depends:', subprocess.check_output(['dpkg-deb', '-f', str(deb), 'Depends'], text=True).strip())
        subprocess.run(['dpkg-deb', '-x', str(deb), str(Path(tmp) / 'payload')], check=True)
        for name in ('cy', 'camayoc'):
            print(name + ':', (Path(tmp) / 'payload/usr/bin' / name).read_text().splitlines()[0])
