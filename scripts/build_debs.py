#!/usr/bin/env python3
"""Repackage published, checksum-verified release archives as .deb files.

The repository builds nothing from source. Each .deb carries the exact bytes a
tool's GitHub release published, after verifying them against that release's
own checksums. A mismatch or a missing checksum refuses the whole tool.
Packages have no maintainer scripts, so installing one never restarts anything.
"""
import argparse
import hashlib
import io
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile

import canary_gate

API = "https://api.github.com/repos/{repo}/releases?per_page=50"
TRIPLES = {"amd64": "x86_64-unknown-linux-gnu", "arm64": "aarch64-unknown-linux-gnu"}


class Refused(Exception):
    pass


def fetch(url: str) -> bytes:
    """Download with curl. Python's TLS reads stall on GitHub's release-asset
    host from some networks while curl does not (measured: headers in 0.17s,
    then a 30s body read timeout; curl fetched the same 5 MB in 0.3s). The token,
    when present, goes in through curl's stdin config, never argv."""
    config = 'user-agent = "scbrown-apt"\n'
    token = os.environ.get("GITHUB_TOKEN")
    if token and url.startswith("https://api.github.com/"):
        config += f'header = "Authorization: Bearer {token}"\n'
    done = subprocess.run(
        ["curl", "--fail", "--silent", "--show-error", "--location", "--proto", "=https",
         "--retry", "4", "--retry-all-errors", "--connect-timeout", "10", "--max-time", "300",
         "--config", "-", url],
        input=config.encode(), capture_output=True, check=False)
    if done.returncode:
        raise OSError(f"curl exit {done.returncode}: {done.stderr.decode().strip()[:200]}")
    return done.stdout


def releases(repo: str, prefix: str, channel: str) -> list[dict]:
    """Published releases for one tag family, newest first."""
    wanted = []
    repository = repo if "/" in repo else "scbrown/" + repo
    for rel in json.loads(fetch(API.format(repo=repository))):
        if rel.get("draft") or not rel["tag_name"].startswith(prefix):
            continue
        if rel.get("prerelease") != (channel == "nightly"):
            continue
        wanted.append(rel)
    return wanted


def asset_name(tool: dict, tag: str, version: str, arch: str) -> str:
    triple = tool.get("triples", {}).get(arch, TRIPLES[arch])
    return tool["asset"].format(tag=tag, ver=version, triple=triple, os="linux", arch=arch)


def expected_sha(tool: dict, rel: dict, name: str) -> str:
    assets = {a["name"]: a["browser_download_url"] for a in rel["assets"]}
    if tool["sums"] == "sidecar":
        if name + ".sha256" not in assets:
            raise Refused(f"{name}: no published .sha256 sidecar")
        text = fetch(assets[name + ".sha256"]).decode()
        return text.split()[0].lower()
    if tool["sums"] not in assets:
        raise Refused(f"{rel['tag_name']}: no published {tool['sums']}")
    for line in fetch(assets[tool["sums"]]).decode().splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1].lstrip("*").split("/")[-1] == name:
            return parts[0].lower()
    raise Refused(f"{name}: not listed in {tool['sums']}")


def extract_bins(archive: bytes, bins: list[str], dest: pathlib.Path) -> None:
    found = {}
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as tar:
        for member in tar.getmembers():
            base = member.name.split("/")[-1]
            if member.isfile() and base in bins and base not in found:
                found[base] = tar.extractfile(member).read()
    missing = [b for b in bins if b not in found]
    if missing:
        raise Refused(f"archive lacks {missing}")
    dest.mkdir(parents=True, exist_ok=True)
    for name, data in found.items():
        path = dest / name
        path.write_bytes(data)
        path.chmod(0o755)


def add_aliases(aliases: dict, bins: list[str], dest: pathlib.Path) -> None:
    """Relative symlinks for a tool's other command names (qp -> quipu)."""
    for alias, target in aliases.items():
        if target not in bins or alias in bins or "/" in alias:
            raise Refused(f"alias {alias} -> {target}: target must be a packaged binary")
        (dest / alias).symlink_to(target)


def runtime_depends(bindir: pathlib.Path, bins: list[str], arch: str) -> str:
    """Inspect ELF requirements without executing release binaries.

    This is deliberately a narrow GNU/Linux ABI policy, not a general shlibs
    resolver. New sonames or newer GCC/C++ symbol families require review.
    Libraries loaded with dlopen (notably ONNX) are outside DT_NEEDED coverage.
    """
    machines = {"amd64": "Advanced Micro Devices X86-64", "arm64": "AArch64"}
    loaders = {"amd64": "ld-linux-x86-64.so.2", "arm64": "ld-linux-aarch64.so.1"}
    libraries = {"libc.so.6": "libc6", "libm.so.6": "libc6",
                 "libpthread.so.0": "libc6", "libdl.so.2": "libc6",
                 "librt.so.1": "libc6", "libgcc_s.so.1": "libgcc-s1",
                 "libstdc++.so.6": "libstdc++6", loaders[arch]: "libc6"}
    allowed = {"GCC_3.0", "GCC_3.3", "GCC_3.3.1", "GCC_3.4", "GCC_3.4.2",
               "GCC_3.4.4", "GCC_4.0.0", "GCC_4.2.0", "GLIBCXX_3.4", "CXXABI_1.3"}
    needed_packages = set()
    glibc = []
    for name in bins:
        binary = bindir / name
        if binary.read_bytes()[:4] != b"\x7fELF":
            raise Refused(f"{name}: expected a Linux ELF binary")
        def readelf(*flags):
            return subprocess.run(["readelf", "--wide", *flags, str(binary)],
                                  check=True, capture_output=True, text=True,
                                  env=dict(os.environ, LC_ALL="C")).stdout
        header = readelf("--file-header")
        machine = re.search(r"^\s*Machine:\s*(.+)$", header, re.M)
        if (not machine or machine[1].strip() != machines[arch] or "ELF64" not in header
                or "little endian" not in header):
            raise Refused(f"{name}: ELF architecture does not match {arch}")
        elf_type = re.search(r"^\s*Type:\s*(\S+)", header, re.M)
        if not elf_type or elf_type[1] not in {"EXEC", "DYN"}:
            raise Refused(f"{name}: ELF is not an executable or PIE")
        program = readelf("--program-headers")
        if not re.search(r"^\s*LOAD\s", program, re.M):
            raise Refused(f"{name}: ELF has no loadable segments")
        interpreter = re.search(r"Requesting program interpreter: ([^]]+)", program)
        interp_paths = {"amd64": {"/lib64/ld-linux-x86-64.so.2",
                                  "/lib/x86_64-linux-gnu/ld-linux-x86-64.so.2"},
                        "arm64": {"/lib/ld-linux-aarch64.so.1",
                                  "/lib/aarch64-linux-gnu/ld-linux-aarch64.so.1"}}
        if interpreter and interpreter[1] not in interp_paths[arch]:
            raise Refused(f"{name}: unqualified ELF interpreter {interpreter[1]}")
        dynamic = readelf("--dynamic")
        sonames = re.findall(r"\(NEEDED\).*?\[([^]]+)\]", dynamic)
        for soname in sonames:
            if soname not in libraries:
                raise Refused(f"{name}: unqualified runtime library {soname}")
            needed_packages.add(libraries[soname])
        # Only version NEEDS, never version definitions or strings in payloads.
        versions = readelf("--version-info")
        needs = versions.split("Version needs section", 1)
        symbols = re.findall(r"\bName: (\S+)", needs[1]) if len(needs) == 2 else []
        binary_glibc = []
        for symbol in symbols:
            if re.fullmatch(r"GLIBC_[0-9]+(?:\.[0-9]+)+", symbol):
                binary_glibc.append(tuple(map(int, symbol[6:].split("."))))
            elif symbol not in allowed:
                raise Refused(f"{name}: unqualified runtime ABI {symbol}")
        if ("libc.so.6" in sonames or interpreter) and not binary_glibc:
            raise Refused(f"{name}: dynamic glibc binary has no numeric ABI requirements")
        glibc.extend(binary_glibc)
    relations = []
    if "libc6" in needed_packages:
        relations.append("libc6 (>= " + ".".join(map(str, max(glibc))) + ")")
    if "libgcc-s1" in needed_packages:
        relations.append("libgcc-s1 (>= 4.2)")
    if "libstdc++6" in needed_packages:
        relations.append("libstdc++6 (>= 4.1.1)")
    return ", ".join(relations)


def release_docs(archive: bytes, bins: list[str], root: pathlib.Path,
                 package: str, tag: str, digest: str) -> None:
    """Carry only documents alongside the selected release executables.

    Never substitute today's source-tree license for a historical release's
    files. Missing upstream documents stay explicit in README.package.
    """
    documents = {"LICENSE": "upstream-license", "LICENSE.txt": "upstream-license",
                 "LICENSE.md": "upstream-license", "COPYING": "upstream-license",
                 "README.md": "README.upstream", "README": "README.upstream",
                 "CHANGELOG.md": "changelog.upstream"}
    found = {}
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as tar:
        members = tar.getmembers()
        parents = {pathlib.PurePosixPath(m.name).parent for m in members
                   if m.isfile() and pathlib.PurePosixPath(m.name).name in bins}
        for member in members:
            path = pathlib.PurePosixPath(member.name)
            if member.isfile() and path.parent in parents and path.name in documents:
                dest = documents[path.name]
                data = tar.extractfile(member).read()
                if dest in found and found[dest] != data:
                    raise Refused(f"{tag}: conflicting release documents for {dest}")
                found[dest] = data
    docdir = root / "usr/share/doc" / package
    docdir.mkdir(parents=True, exist_ok=True)
    for name, data in found.items():
        (docdir / name).write_bytes(data)
    (docdir / "README.package").write_text(
        f"Checksum-verified upstream release: {tag}\nArchive SHA256: {digest}\n"
        "Binaries are repackaged unchanged; no source build is claimed.\n"
        "Depends covers inspected ELF DT_NEEDED and version requirements only.\n"
        "Dynamically loaded libraries, models and optional integrations require separate qualification.\n"
        "No services, state directories, conffiles or activation scripts are installed.\n"
        "Included upstream documents: " + (", ".join(sorted(found)) or "none") + "\n"
        "Missing release documents are not replaced with an unverified source-tree license.\n")


def control_text(package: str, version: str, arch: str, homepage: str, relations: dict,
                 description: str, detail: list[str]) -> str:
    lines = [f"Package: {package}", f"Version: {version}", f"Architecture: {arch}",
             "Maintainer: Steve Brown <scbrown@users.noreply.github.com>",
             f"Homepage: {homepage}", "Section: utils", "Priority: optional"]
    lines += [f"{field}: {value}" for field, value in relations.items()]
    lines.append(f"Description: {description}")
    lines += [f" {line}" for line in detail]
    return "\n".join(lines) + "\n"


def pack(root: pathlib.Path, control: str, deb: pathlib.Path) -> None:
    (root / "DEBIAN").mkdir(parents=True, exist_ok=True)
    (root / "DEBIAN/control").write_text(control)
    deb.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["dpkg-deb", "--root-owner-group", "-Zxz", "--build", str(root), str(deb)],
                   check=True, stdout=subprocess.DEVNULL)


def deb_version(tool: dict, version: str) -> str:
    """The upstream version, plus the manifest's Debian revision when it has one.
    A revision lets a packaging change (new aliases) reach an already-published
    upstream version without rebuilding an immutable .deb under its old name."""
    rev = tool.get("debian_revision")
    return f"{version}-{rev}" if rev else version


def build(tool_id: str, tool: dict, rel: dict, arch: str, out: pathlib.Path) -> pathlib.Path | None:
    tag = rel["tag_name"]
    version = tag[len(tool["tag_prefix"]):]
    debver = deb_version(tool, version)
    deb = out / f"{tool['package']}_{debver}_{arch}.deb"
    if deb.exists():
        return None  # published versions are immutable; never rebuild
    name = asset_name(tool, tag, version, arch)
    assets = {a["name"]: a["browser_download_url"] for a in rel["assets"]}
    if name not in assets:
        raise Refused(f"{tag}: no published {name}")
    data = fetch(assets[name])
    got = hashlib.sha256(data).hexdigest()
    want = expected_sha(tool, rel, name)
    if got != want:
        raise Refused(f"{name}: sha256 {got} != published {want}")
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp) / "pkg"
        extract_bins(data, tool["bins"], root / "usr/bin")
        add_aliases(tool.get("aliases", {}), tool["bins"], root / "usr/bin")
        relations = dict(tool.get("relations", {}))
        depends = runtime_depends(root / "usr/bin", tool["bins"], arch)
        if depends:
            relations["Depends"] = ", ".join(filter(None, [relations.get("Depends"), depends]))
        release_docs(data, tool["bins"], root, tool["package"], tag, got)
        pack(root, control_text(
            tool["package"], debver, arch, "https://github.com/" + tool.get("repository", "scbrown/" + tool_id),
            relations, tool["description"],
            [f"Repackaged from the checksum-verified {tag} release archive.",
             f"Upstream archive sha256: {got}"]), deb)
    return deb


def promote_seeds(tool, rel, arch, pool, trust):
    """Stable seeds is promoted from the canary bytes, never independently built."""
    version = rel["tag_name"][len(tool["tag_prefix"]):]
    filename = f"seeds_{deb_version(tool, version)}_{arch}.deb"
    canary = pool / "canary" / "seeds" / filename
    if not canary.is_file():
        raise Refused("seeds candidate missing from canary")
    candidate = canary_gate.package_identity(canary)
    # Resolve the actual tag, not target_commitish (which may be a moving branch).
    repository = tool.get("repository", canary_gate.REPOSITORY)
    commit = json.loads(fetch("https://api.github.com/repos/" + repository + "/commits/" + rel["tag_name"]))["sha"]
    candidate.update(repository=repository, tag=rel["tag_name"], commit=commit)
    if candidate["version"] != deb_version(tool, version) or candidate["architecture"] != arch:
        raise Refused("seeds canary package differs from release")
    name = asset_name(tool, rel["tag_name"], version, arch)
    if candidate["archive_sha256"] != expected_sha(tool, rel, name):
        raise Refused("seeds canary archive differs from published release")
    # An Ops runner can consume this public, exact-artifact identity before signing.
    canary.with_suffix(".candidate.json").write_text(json.dumps(candidate, sort_keys=True, indent=2) + "\n")
    return canary_gate.promote(canary, pool / "stable" / "seeds" / filename, candidate, trust)


def satisfying_target(depends: str, arch: str, pool: pathlib.Path) -> pathlib.Path | None:
    """A .deb already in the pool that satisfies `name (>= version)` for arch."""
    match = re.fullmatch(r"([a-z0-9.+-]+) \(>= ([^)\s]+)\)", depends)
    if not match:
        raise Refused(f"unsupported transitional dependency {depends!r}")
    name, minimum = match.groups()
    for candidate in sorted((pool / name).glob(f"{name}_*.deb")):
        fields = subprocess.run(["dpkg-deb", "--field", str(candidate), "Package", "Version", "Architecture"],
                                check=True, capture_output=True, text=True).stdout
        info = dict(line.split(": ", 1) for line in fields.splitlines() if ": " in line)
        if (info.get("Package") == name and info.get("Architecture") in (arch, "all")
                and subprocess.run(["dpkg", "--compare-versions", info.get("Version", ""), "ge", minimum]).returncode == 0):
            return candidate
    return None


def build_transitional(tool_id: str, tool: dict, arch: str, pool: pathlib.Path) -> pathlib.Path | None:
    """A file-less package that keeps an old package name installable and moves
    its users to this tool (caboodle -> tambo). It has a fixed version, so it is
    built once and, like every other .deb here, never rebuilt."""
    spec = tool["transitional"]
    deb = pool / spec["package"] / f"{spec['package']}_{spec['version']}_{arch}.deb"
    if deb.exists():
        return None
    # Never publish a package whose dependency the pool cannot satisfy: the
    # publisher signs a partial pool when another build refused (aegis-0ev review).
    if not satisfying_target(spec["depends"], arch, pool):
        raise Refused(f"transitional {spec['package']} {spec['version']}: no verified "
                      f"{arch} package satisfying '{spec['depends']}' in the pool")
    with tempfile.TemporaryDirectory() as tmp:
        pack(pathlib.Path(tmp) / "pkg", control_text(
            spec["package"], spec["version"], arch, f"https://github.com/scbrown/{tool_id}",
            {"Depends": spec["depends"]}, f"transitional package for {tool['package']}",
            [f"{spec['package']} is now {tool['package']}; this package only installs it.",
             "It can be removed once nothing depends on it."]), deb)
    return deb


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=pathlib.Path, default=pathlib.Path("tools.json"))
    parser.add_argument("--pool", type=pathlib.Path, required=True)
    parser.add_argument("--channel", choices=["stable", "nightly", "canary"], default="stable")
    parser.add_argument("--canary-trust", type=pathlib.Path, default=canary_gate.DEFAULT_TRUST)
    parser.add_argument("--only", nargs="*", help="limit to these manifest keys")
    parser.add_argument("--latest", type=int, default=1, help="releases per tool to keep building")
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text())
    failures = 0
    for tool_id, tool in manifest.items():
        if tool_id.startswith("_"):
            continue
        if args.only and tool_id not in args.only:
            continue
        if args.channel == "canary" and tool_id != "seeds":
            continue
        try:
            rels = releases(tool.get("repository", tool_id), tool["tag_prefix"], args.channel)[: args.latest]
        except Exception as error:  # an unreadable API is UNKNOWN, not "no release"
            print(f"UNKNOWN {tool_id}: {error}", file=sys.stderr)
            failures += 1
            continue
        if not rels:
            print(f"none {tool_id} ({args.channel})")
        for rel in rels:
            for arch in tool["arches"]:
                try:
                    if args.channel == "stable" and tool["package"] == "seeds":
                        deb = promote_seeds(tool, rel, arch, args.pool, args.canary_trust)
                    else:
                        deb = build(tool_id, tool, rel, arch, args.pool / args.channel / tool["package"])
                    print(f"{'built' if deb else 'exists'} {tool_id} {rel['tag_name']} {arch}")
                except (Refused, canary_gate.Refused) as error:
                    print(f"REFUSED {tool_id} {rel['tag_name']} {arch}: {error}", file=sys.stderr)
                    failures += 1
                except Exception as error:  # network or API failure: UNKNOWN, never "built"
                    print(f"UNKNOWN {tool_id} {rel['tag_name']} {arch}: {error}", file=sys.stderr)
                    failures += 1
        # Only a channel that has releases of this tool can carry its transition
        # (tambo publishes no prereleases, so nightly has nothing to depend on).
        if rels and "transitional" in tool:
            for arch in tool["arches"]:
                try:
                    deb = build_transitional(tool_id, tool, arch, args.pool / args.channel)
                    print(f"{'built' if deb else 'exists'} {tool['transitional']['package']} (transitional) {arch}")
                except Refused as error:
                    print(f"REFUSED {tool_id} {error}", file=sys.stderr)
                    failures += 1
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
