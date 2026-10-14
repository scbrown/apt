#!/usr/bin/env python3
"""Write Packages and Release indexes for a flat pool, one suite per channel.

Layout (Debian "dists" form, so a standard sources line works):
  pool/<channel>/<package>/<package>_<version>_<arch>.deb
  dists/<channel>/main/binary-<arch>/Packages{,.gz}
  dists/<channel>/Release            (signed separately: InRelease, Release.gpg)
Output is deterministic for a given pool, so an unchanged pool republishes
byte-identical indexes.
"""
import argparse
import email.utils
import gzip
import hashlib
import io
import pathlib
import subprocess
import sys

ARCHES = ["amd64", "arm64"]


def control(deb: pathlib.Path) -> str:
    return subprocess.run(["dpkg-deb", "--field", str(deb)], check=True,
                          capture_output=True, text=True).stdout.rstrip("\n")


def stanza(deb: pathlib.Path, root: pathlib.Path) -> tuple[str, str]:
    data = deb.read_bytes()
    fields = control(deb)
    arch = next(l.split(":", 1)[1].strip() for l in fields.splitlines() if l.startswith("Architecture:"))
    text = (fields + "\n"
            f"Filename: {deb.relative_to(root).as_posix()}\n"
            f"Size: {len(data)}\n"
            f"MD5sum: {hashlib.md5(data).hexdigest()}\n"
            f"SHA256: {hashlib.sha256(data).hexdigest()}\n")
    return arch, text


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=pathlib.Path, required=True)
    parser.add_argument("--channel", required=True)
    parser.add_argument("--date", help="RFC 2822 date for Release (default: newest .deb mtime)")
    args = parser.parse_args()
    root = args.root
    debs = sorted((root / "pool" / args.channel).rglob("*.deb"))
    by_arch = {a: [] for a in ARCHES}
    for deb in debs:
        arch, text = stanza(deb, root)
        by_arch.setdefault(arch, []).append(text)
    dist = root / "dists" / args.channel
    files = []
    for arch in ARCHES:
        body = "\n".join(by_arch.get(arch, [])).encode()
        d = dist / "main" / f"binary-{arch}"
        d.mkdir(parents=True, exist_ok=True)
        (d / "Packages").write_bytes(body)
        buf = io.BytesIO()
        with gzip.GzipFile(fileobj=buf, mode="wb", mtime=0) as gz:
            gz.write(body)
        (d / "Packages.gz").write_bytes(buf.getvalue())
        for name in ("Packages", "Packages.gz"):
            blob = (d / name).read_bytes()
            files.append((f"main/binary-{arch}/{name}", blob))
    when = args.date or email.utils.formatdate(
        max((p.stat().st_mtime for p in debs), default=0), usegmt=True)
    lines = [
        "Origin: scbrown",
        "Label: scbrown stack",
        f"Suite: {args.channel}",
        f"Codename: {args.channel}",
        f"Date: {when}",
        "Architectures: " + " ".join(ARCHES),
        "Components: main",
        "Description: Quipu tool stack, repackaged from checksum-verified releases",
        "SHA256:",
    ]
    for name, blob in files:
        lines.append(f" {hashlib.sha256(blob).hexdigest()} {len(blob):>16} {name}")
    text = "\n".join(lines) + "\n"
    release = dist / "Release"
    strip = lambda t: "\n".join(l for l in t.splitlines() if not l.startswith("Date:"))
    # A fresh checkout gives every .deb a new mtime. Keep the old Release (and so
    # the old signature) when nothing but the Date would change.
    if release.exists() and strip(release.read_text()) == strip(text):
        print(f"{args.channel}: {len(debs)} packages, Release unchanged")
        return 0
    release.write_text(text)
    print(f"{args.channel}: {len(debs)} packages, Release written")
    return 0


if __name__ == "__main__":
    sys.exit(main())
