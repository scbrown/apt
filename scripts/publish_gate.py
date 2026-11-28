#!/usr/bin/env python3
"""Final gate of the publish workflow: fail on any refused or unknown release EXCEPT one state.

The one expected state is "seeds promotion pending": stable seeds refused with exactly
`unavailable-canary-evidence` because NO signed canary receipt names that exact release or
deb. It is reported as a warning and does not fail the run. Everything else still fails:
another tool, another reason, an UNKNOWN, a refusal for a version that HAS a receipt (an
expired or invalid receipt is not "pending"), a non-zero exit with no recorded refusal, a
traceback, a refusal outside the stable channel, or a missing log.

Inputs are the captured output and exit status of each build and index command:
<gate-dir>/{build,index}-{canary,stable,nightly}.{log,rc}.
"""
import argparse
import json
import os
import pathlib
import re
import sys

CHANNELS = ("canary", "stable", "nightly")
EXPECTED_REASON = "unavailable-canary-evidence"
BUILD_EXPECTED = re.compile(r"^REFUSED seeds (seeds-ai-v\S+) (amd64|arm64): " + EXPECTED_REASON + r"$")
INDEX_EXPECTED = re.compile(r"^REFUSED stable seeds_(\S+)_(amd64|arm64)\.deb: " + EXPECTED_REASON + r"$")
FAILURE_LINE = re.compile(r"^(REFUSED|UNKNOWN)\b")


def receipt_names(receipts: pathlib.Path):
    """(tags, versions) that some receipt names, whatever that receipt's validity."""
    tags, versions = set(), set()
    if not receipts.is_dir():
        raise ValueError(f"receipts directory unreadable: {receipts}")
    for path in sorted(receipts.glob("*.json")):
        candidate = json.loads(path.read_text()).get("candidate", {})
        if candidate.get("tag"):
            tags.add(candidate["tag"])
        if candidate.get("version"):
            versions.add(candidate["version"])
    return tags, versions


def read(gate: pathlib.Path, kind: str, channel: str):
    log, rc = gate / f"{kind}-{channel}.log", gate / f"{kind}-{channel}.rc"
    if not log.is_file() or not rc.is_file():
        raise ValueError(f"missing {kind}-{channel} log or exit status")
    return log.read_text(errors="replace"), int(rc.read_text().strip())


def judge(kind, channel, text, rc, tags, versions, problems, pending):
    where = f"{kind} {channel}"
    lines = [line.rstrip() for line in text.splitlines()]
    failures = [line for line in lines if FAILURE_LINE.match(line)]
    if rc == 0:
        if failures:
            problems.append(f"{where}: exit 0 but recorded {len(failures)} refusal/unknown line(s)")
        return
    if kind == "index" and rc != 3:
        problems.append(f"{where}: exit {rc} (only 0 and 3 are index outcomes)")
        return
    if channel != "stable":
        problems.append(f"{where}: exit {rc}; only the stable channel can carry the expected state")
        return
    if any(line.startswith("Traceback") for line in lines):
        problems.append(f"{where}: exit {rc} with a traceback")
        return
    if not failures:
        problems.append(f"{where}: exit {rc} with no recorded refusal; cause unknown")
        return
    pattern = BUILD_EXPECTED if kind == "build" else INDEX_EXPECTED
    for line in failures:
        match = pattern.match(line)
        if not match:
            problems.append(f"{where}: unexpected: {line}")
        elif match.group(1) in (tags if kind == "build" else versions):
            problems.append(f"{where}: a receipt names {match.group(1)} but it is still refused: {line}")
        else:
            pending.add(match.group(1))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gate-dir", type=pathlib.Path, required=True)
    parser.add_argument("--policy", type=pathlib.Path, required=True)
    parser.add_argument("--receipts", type=pathlib.Path, required=True)
    args = parser.parse_args()
    problems, pending = [], set()
    try:
        json.loads(args.policy.read_text())
        tags, versions = receipt_names(args.receipts)
        for kind in ("build", "index"):
            for channel in CHANNELS:
                text, rc = read(args.gate_dir, kind, channel)
                judge(kind, channel, text, rc, tags, versions, problems, pending)
    except (OSError, ValueError) as error:
        problems.append(f"cannot judge the run: {error}")
    if problems:
        print("at least one release was refused or unreadable beyond the expected seeds state:")
        for problem in problems:
            print(f"  FAIL {problem}")
        return 1
    if pending:
        names = ", ".join(sorted(pending))
        message = (f"seeds promotion pending: no signed canary receipt names {names}; "
                   "stable seeds stays at its last admitted version until Ops signs one")
        print(f"::warning title=seeds promotion pending::{message}")
        print(f"SEEDS-PROMOTION-PENDING {names}")
        summary = os.environ.get("GITHUB_STEP_SUMMARY")
        if summary:
            with open(summary, "a") as handle:
                handle.write(f"### Seeds promotion pending\n\n{message}\n")
    else:
        print("no refused or unknown release")
    return 0


if __name__ == "__main__":
    sys.exit(main())
