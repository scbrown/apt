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
import datetime
import email.utils
import email.parser
import gzip
import hashlib
import io
import pathlib
import re
import subprocess
import sys
import tempfile

import canary_gate

ARCHES = ["amd64", "arm64"]
DEFAULT_PUBLISHED_KEY = pathlib.Path(__file__).resolve().parents[1] / "key.gpg"
MAX_INDEX_BYTES = 8 * 1024 * 1024
# A signed Release expires so a stale copy cannot be replayed to clients; it is
# re-signed well before expiry even when no package changed.
VALID_DAYS = 10
REFRESH_DAYS = 3


def single(message, field):
    values = message.get_all(field, [])
    if len(values) != 1:
        raise canary_gate.Refused("invalid-published-field: " + field)
    return values[0]


def read_published(root, path):
    if not path.resolve().is_relative_to(root.resolve()) or path.stat().st_size > MAX_INDEX_BYTES:
        raise canary_gate.Refused("invalid-published-path-or-size")
    return path.read_bytes()


def published_seeds(root, keyring):
    """Exact legacy bytes from the previous signed, unexpired stable index.

    This is availability preservation during explicitly empty enrollment, not a
    smoke receipt. Promotion never calls this reader. No pool or index writes.
    """
    signed = root / "dists/stable/InRelease"
    if not read_published(root, signed).startswith(b"-----BEGIN PGP SIGNED MESSAGE-----"):
        raise canary_gate.Refused("missing-clearsigned-published-release")
    # Never supplement this repository's public key with the caller's keyring.
    with tempfile.TemporaryDirectory() as home:
        verified = subprocess.run(["gpgv", "--homedir", home, "--keyring", str(keyring.resolve()),
                                   "--output", "-", str(signed)],
                                  check=True, capture_output=True, timeout=10)
    release = email.parser.Parser().parsestr(verified.stdout.decode())
    if single(release, "Suite") != "stable":
        raise canary_gate.Refused("wrong-published-suite")
    now = datetime.datetime.now(datetime.timezone.utc)
    date = email.utils.parsedate_to_datetime(single(release, "Date"))
    until = email.utils.parsedate_to_datetime(single(release, "Valid-Until"))
    if date.tzinfo is None or until.tzinfo is None or not date <= now < until:
        raise canary_gate.Refused("expired-or-future-published-release")
    hashes = {}
    for line in single(release, "SHA256").splitlines():
        parts = line.split()
        if not parts:
            continue
        if (len(parts) != 3 or not re.fullmatch(r"[0-9a-f]{64}", parts[0])
                or not parts[1].isdigit() or parts[2] in hashes):
            raise canary_gate.Refused("invalid-published-index-digest")
        hashes[parts[2]] = (int(parts[1]), parts[0])
    retained = {}
    for arch in ARCHES:
        name = f"main/binary-{arch}/Packages"
        data = read_published(root, root / "dists/stable" / name)
        if hashes.get(name) != (len(data), hashlib.sha256(data).hexdigest()):
            raise canary_gate.Refused("published-index-digest-mismatch")
        for block in data.decode().split("\n\n"):
            if not block.strip():
                continue
            entry = email.parser.Parser().parsestr(block)
            if single(entry, "Package") != "seeds":
                continue
            filename = single(entry, "Filename")
            version = single(entry, "Version")
            digest = single(entry, "SHA256")
            size = single(entry, "Size")
            if (single(entry, "Architecture") != arch or not size.isdigit()
                    or not re.fullmatch(r"[0-9a-f]{64}", digest)
                    or filename != f"pool/stable/seeds/seeds_{version}_{arch}.deb"
                    or "/" in version or version in ("", ".", "..") or filename in retained):
                raise canary_gate.Refused("invalid-published-seeds-entry")
            deb = root / filename
            if (not deb.resolve().is_relative_to(root.resolve()) or not deb.is_file()
                    or deb.stat().st_size != int(size) or canary_gate.sha256(deb) != digest):
                raise canary_gate.Refused("published-seeds-bytes-missing-or-changed")
            retained[filename] = (int(size), digest)
    return retained


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
    parser.add_argument("--canary-trust", type=pathlib.Path, default=canary_gate.DEFAULT_TRUST)
    parser.add_argument("--published-keyring", type=pathlib.Path, default=DEFAULT_PUBLISHED_KEY,
                        help="existing apt repository public key for pre-enrollment retention")
    args = parser.parse_args()
    root = args.root
    debs = sorted((root / "pool" / args.channel).rglob("*.deb"))
    # Validate the complete old baseline before changing ANY metadata. An
    # unreadable signature/index or changed old .deb is not an empty baseline.
    retained = {}
    try:
        if args.channel == "stable" and canary_gate.policy(args.canary_trust).get("retain_published_seeds"):
            # A brand-new repository containing only other tools has nothing
            # to retain. Any existing signed repository must be verified even
            # when all its old Seeds files have gone missing from the pool.
            if ((root / "dists/stable/InRelease").exists()
                    or any(single(email.parser.Parser().parsestr(control(d)), "Package") == "seeds"
                           for d in debs)):
                retained = published_seeds(root, args.published_keyring)
    except (canary_gate.Refused, OSError, ValueError, TypeError, subprocess.SubprocessError) as error:
        print(f"REFUSED stable retention: {error}", file=sys.stderr)
        return 2
    by_arch = {a: [] for a in ARCHES}
    refused = 0
    for deb in debs:
        if args.channel == "stable":
            try:
                canary_gate.verify(deb, args.canary_trust)
            except canary_gate.Refused as error:
                key = deb.relative_to(root).as_posix()
                current = (deb.stat().st_size, canary_gate.sha256(deb))
                if key in retained and retained[key] != current:
                    print(f"REFUSED changed retained stable {deb.name}", file=sys.stderr)
                    return 2
                if retained.get(key) == current:
                    print(f"RETAIN published stable {deb.name}; not canary admission", file=sys.stderr)
                else:
                    print(f"REFUSED stable {deb.name}: {error}", file=sys.stderr)
                    refused += 1
                    continue
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
    now = datetime.datetime.now(datetime.timezone.utc)
    when = args.date or email.utils.format_datetime(now, usegmt=True)
    until = email.utils.format_datetime(now + datetime.timedelta(days=VALID_DAYS), usegmt=True)
    lines = [
        "Origin: scbrown",
        "Label: scbrown stack",
        f"Suite: {args.channel}",
        f"Codename: {args.channel}",
        f"Date: {when}",
        f"Valid-Until: {until}",
        "Architectures: " + " ".join(ARCHES),
        "Components: main",
        "Description: Quipu tool stack, repackaged from checksum-verified releases",
        "SHA256:",
    ]
    for name, blob in files:
        lines.append(f" {hashlib.sha256(blob).hexdigest()} {len(blob):>16} {name}")
    text = "\n".join(lines) + "\n"
    release = dist / "Release"
    dated = ("Date:", "Valid-Until:")
    strip = lambda t: "\n".join(l for l in t.splitlines() if not l.startswith(dated))
    # Keep the old Release (and its signature) when only the dates would change
    # and it stays valid for more than REFRESH_DAYS; otherwise refresh it.
    if release.exists() and strip(release.read_text()) == strip(text):
        old_until = next((l.split(":", 1)[1].strip() for l in release.read_text().splitlines()
                          if l.startswith("Valid-Until:")), None)
        if old_until and email.utils.parsedate_to_datetime(old_until) - now > datetime.timedelta(days=REFRESH_DAYS):
            print(f"{args.channel}: {len(debs)} packages, Release unchanged")
            return 3 if refused else 0
    release.write_text(text)
    print(f"{args.channel}: {len(debs)} packages, Release written")
    return 3 if refused else 0


if __name__ == "__main__":
    sys.exit(main())
