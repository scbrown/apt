#!/usr/bin/env python3
"""Checksum admission and offline Debian payload controls for stdlib Python tools."""
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
spec = importlib.util.spec_from_file_location("build_debs", ROOT / "scripts/build_debs.py")
b = importlib.util.module_from_spec(spec)
spec.loader.exec_module(b)
TOOL = json.loads((ROOT / "tools.json").read_text())["camayoc"]


def archive(extra=()):
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode="w:gz") as tar:
        for name, content, kind in [
            ("release/pyproject.toml", b'[project]\nname="camayoc"\nversion="0.1.6"\nrequires-python=">=3.10"\ndependencies=[]\n[project.scripts]\ncamayoc="camayoc_cli.cli:main"\ncy="camayoc_cli.cli:main"\n', tarfile.REGTYPE),
            ("release/camayoc_cli/__init__.py", b"", tarfile.REGTYPE),
            ("release/camayoc_cli/cli.py", b"def main(): return 0", tarfile.REGTYPE),
            ("release/camayoc_cli/data/definitions.json", b"{}", tarfile.REGTYPE),
            *extra,
        ]:
            item = tarfile.TarInfo(name)
            item.type = kind
            item.size = len(content) if kind == tarfile.REGTYPE else 0
            item.linkname = "/tmp/should-not-exist" if kind == tarfile.SYMTYPE else ""
            tar.addfile(item, io.BytesIO(content) if kind == tarfile.REGTYPE else None)
    return out.getvalue()


class PythonPackage(unittest.TestCase):
    def build(self, dest, data, digest=None):
        name = "camayoc-v0.1.6.tar.gz"
        rel = {"tag_name": "v0.1.6", "assets": [
            {"name": name, "browser_download_url": "archive"},
            {"name": "SHA256SUMS", "browser_download_url": "sums"}]}
        checksum = digest or hashlib.sha256(data).hexdigest()
        with patch.object(b, "fetch", side_effect=lambda url: data if url == "archive" else
                          f"{checksum}  {name}\n".encode()):
            return b.build("camayoc", TOOL, rel, "amd64", dest)

    def test_real_deb_contains_data_and_both_isolated_entrypoints(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            deb = self.build(root / "pool", archive())
            depends = subprocess.check_output(["dpkg-deb", "-f", str(deb), "Depends"], text=True)
            self.assertEqual(depends.strip(), "python3 (>= 3.10)")
            subprocess.run(["dpkg-deb", "-x", str(deb), str(root / "unpack")], check=True)
            payload = root / "unpack"
            for name in ("cy", "camayoc"):
                self.assertTrue((payload / "usr/bin" / name).stat().st_mode & 0o111)
                self.assertEqual((payload / "usr/bin" / name).read_text(),
                                 "#!/usr/bin/python3 -I\nfrom camayoc_cli.cli import main\nraise SystemExit(main())\n")
            self.assertEqual((payload / "usr/lib/python3/dist-packages/camayoc_cli/data/definitions.json").read_bytes(), b"{}")
            self.assertIsNone(self.build(root / "pool", archive()))

    def test_bad_checksum_never_creates_deb(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with self.assertRaises(b.Refused):
                self.build(root, archive(), "0" * 64)
            self.assertEqual(list(root.glob("*.deb")), [])

    def test_rejects_unsafe_duplicate_link_and_native_payload(self):
        for extra in [
            [("../escape", b"x", tarfile.REGTYPE)],
            [("release/camayoc_cli/cli.py", b"x", tarfile.REGTYPE)],
            [("release/camayoc_cli/data/link.json", b"", tarfile.SYMTYPE)],
            [("release/camayoc_cli/native.so", b"x", tarfile.REGTYPE)],
            [("other/camayoc_cli/extra.py", b"x", tarfile.REGTYPE)],
        ]:
            with self.subTest(extra=extra), tempfile.TemporaryDirectory() as tmp:
                with self.assertRaises(b.Refused):
                    self.build(Path(tmp), archive(extra))
                self.assertEqual(list(Path(tmp).glob("*.deb")), [])

    def test_dependency_or_runtime_change_is_not_silently_published(self):
        for old, new in [(b"dependencies=[]", b'dependencies=["requests"]'),
                         (b">=3.10", b">=3.11"), (b'0.1.6', b'0.1.7')]:
            data = archive()
            out = io.BytesIO()
            with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as source, tarfile.open(fileobj=out, mode="w:gz") as target:
                for member in source:
                    content = source.extractfile(member).read()
                    if member.name.endswith("pyproject.toml"):
                        content = content.replace(old, new)
                    member.size = len(content)
                    target.addfile(member, io.BytesIO(content))
            with self.subTest(change=new), tempfile.TemporaryDirectory() as tmp:
                with self.assertRaises(b.Refused):
                    self.build(Path(tmp), out.getvalue())
                self.assertEqual(list(Path(tmp).glob("*.deb")), [])

    def test_unknown_kind_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(TOOL, {"kind": "unknown"}):
            with self.assertRaises(b.Refused):
                self.build(Path(tmp), archive())


if __name__ == "__main__":
    unittest.main()
