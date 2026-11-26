#!/usr/bin/env python3
"""Bound the served pool: keep the newest versions of each package, plus pins.

GitHub Pages refuses a site artifact over 1 GB. The pool only ever grew (every
release of every tool, both arches), so the served tree reached 3.6 GB and the
Pages deploy began to fail, freezing the served index fleet-wide (aegis-g68w.19).

Retention, per pool/<channel>/<package>/:
  * the newest `keep` versions (Debian version order), all arches;
  * every version named in retention.json "protect" (declared rollback targets);
  * every .deb the current signed stable index lists for Seeds, because the
    indexer's stable retention refuses when one of those exact files is missing.
Everything else is removed from the working tree only. The bytes remain in the
gh-pages history; nothing here rewrites history or touches signatures.
"""
import argparse
import email.parser
import functools
import json
import pathlib
import re
import subprocess
import sys

DEB = re.compile(r"^(?P<pkg>[a-z0-9][a-z0-9+.-]*)_(?P<ver>[0-9][A-Za-z0-9.+~:-]*)_(?P<arch>[a-z0-9]+)\.deb$")


def compare(a: str, b: str) -> int:
    if a == b:
        return 0
    lt = subprocess.run(["dpkg", "--compare-versions", a, "lt", b], check=False).returncode == 0
    return -1 if lt else 1


def indexed_stable_seeds(root: pathlib.Path) -> set[str]:
    keep = set()
    for packages in (root / "dists/stable/main").glob("binary-*/Packages"):
        for block in packages.read_text().split("\n\n"):
            if not block.strip():
                continue
            entry = email.parser.Parser().parsestr(block)
            if entry.get("Package") == "seeds" and entry.get("Filename"):
                keep.add(entry["Filename"])
    return keep


def plan(root: pathlib.Path, keep: int, protect: dict[str, list[str]]):
    pool = root / "pool"
    pinned_files = indexed_stable_seeds(root)
    removals, kept, missing_pins = [], [], []
    for group in sorted(p for p in pool.glob("*/*") if p.is_dir()):
        name = group.relative_to(pool).as_posix()  # "<channel>/<package>"
        debs = {}
        for deb in group.glob("*.deb"):
            m = DEB.match(deb.name)
            if not m:
                raise SystemExit(f"REFUSED unrecognised pool file {deb}")
            debs.setdefault(m["ver"], []).append(deb)
        order = sorted(debs, key=functools.cmp_to_key(compare))
        wanted = set(order[-keep:]) | set(protect.get(name, []))
        missing_pins += [f"{name} {v}" for v in protect.get(name, []) if v not in debs]
        for version in order:
            for deb in sorted(debs[version]):
                rel = deb.relative_to(root).as_posix()
                (kept if version in wanted or rel in pinned_files else removals).append(deb)
    return removals, kept, missing_pins


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=pathlib.Path, required=True, help="site root containing pool/ and dists/")
    parser.add_argument("--retention", type=pathlib.Path, required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    policy = json.loads(args.retention.read_text())
    keep = int(policy["keep"])
    if keep < 2:
        raise SystemExit("REFUSED keep < 2 leaves no rollback version")
    protect = {k: [p["version"] for p in v] for k, v in policy.get("protect", {}).items()}
    removals, kept, missing = plan(args.root, keep, protect)
    for pin in missing:
        # A pin absent from the pool is reported, never fabricated or fatal: it may
        # predate this repository or already be absent upstream.
        print(f"WARN protected version not in pool: {pin}", file=sys.stderr)
    freed = sum(d.stat().st_size for d in removals)
    total = freed + sum(d.stat().st_size for d in kept)
    for deb in removals:
        print(("would remove " if args.dry_run else "remove ") + deb.relative_to(args.root).as_posix())
        if not args.dry_run:
            deb.unlink()
    print(f"pool: kept {len(kept)} debs, {'would remove' if args.dry_run else 'removed'} "
          f"{len(removals)} ({freed} of {total} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
