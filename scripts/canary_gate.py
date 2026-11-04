"""Seeds stable admission. Only an Ops-signed, exact-deb canary PASS admits it.

This module never installs packages, writes to a board, or holds a private key.
The signature covers the exact JSON file bytes, not a re-serialization.
"""
import datetime as dt
import hashlib
import json
import os
import pathlib
import re
import shutil
import subprocess
import tempfile

DEFAULT_TRUST = pathlib.Path(__file__).resolve().parents[1] / "canary"
CONTRACT = "seeds-canary-v1"
CHECKS = {"version", "show", "identity", "st_comment", "author_actor_readback",
          "rollback_ready", "hold_restored"}
HEX = re.compile(r"[0-9a-f]{64}")
NAME = re.compile(r"[a-z0-9][a-z0-9-]{0,63}")


class Refused(Exception):
    pass


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise Refused("duplicate-json-key")
        result[key] = value
    return result


def _json(data):
    try:
        return json.loads(data, object_pairs_hook=_pairs)
    except (ValueError, UnicodeError) as error:
        raise Refused("invalid-json") from error


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def package_identity(deb):
    """Inspect the actual package, including its verified upstream archive hash."""
    fields = subprocess.run(["dpkg-deb", "--field", str(deb)], check=True,
                            capture_output=True, text=True).stdout
    def field(name):
        values = [s[len(name) + 2:] for s in fields.splitlines() if s.startswith(name + ": ")]
        if len(values) != 1:
            raise Refused("invalid-package-field")
        return values[0]
    identity = {"package": field("Package"), "version": field("Version"),
                "architecture": field("Architecture"), "deb_sha256": sha256(deb)}
    if identity["package"] == "seeds":
        hashes = re.findall(r"^ Upstream archive sha256: ([0-9a-f]{64})$", fields, re.M)
        if len(hashes) != 1:
            raise Refused("missing-archive-digest")
        identity["archive_sha256"] = hashes[0]
    return identity


def _time(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", value):
        raise Refused("invalid-receipt-time")
    try:
        return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise Refused("invalid-receipt-time") from error


def verify(deb, trust=DEFAULT_TRUST, *, candidate=None, promotion=False, now=None):
    """Return the admitted identity, or refuse without changing pool/index files.

    A fresh receipt is required to promote; retained stable bytes remain admitted
    only while their signature AND current contract/canary policy still match.
    """
    identity = package_identity(deb)
    if identity["package"] != "seeds":
        return identity
    try:
        policy = _json((trust / "policy.json").read_bytes())
        if set(policy) != {"contract", "keys", "canaries"} or policy["contract"] != CONTRACT:
            raise Refused("invalid-policy")
        if not isinstance(policy["keys"], dict) or not isinstance(policy["canaries"], dict):
            raise Refused("invalid-policy")
        stem = trust / "receipts" / identity["deb_sha256"]
        raw = stem.with_suffix(".json").read_bytes()
        signature = stem.with_suffix(".sig").read_bytes()
        if len(raw) > 32768 or len(signature) != 64:
            raise Refused("invalid-receipt-size")
        receipt = _json(raw)
        required = {"contract", "key_id", "canary_id", "topology_sha256", "st_version",
                    "measured_at", "expires_at", "candidate", "checks", "outcome", "evidence_sha256"}
        if not isinstance(receipt, dict) or set(receipt) != required:
            raise Refused("invalid-receipt-fields")
        key_id, canary_id = receipt["key_id"], receipt["canary_id"]
        if not isinstance(key_id, str) or not NAME.fullmatch(key_id):
            raise Refused("invalid-key-id")
        if not isinstance(canary_id, str) or not NAME.fullmatch(canary_id):
            raise Refused("invalid-canary-id")
        key = policy["keys"].get(key_id)
        host = policy["canaries"].get(canary_id)
        if not isinstance(key, dict) or set(key) != {"public_key_sha256"}:
            raise Refused("unenrolled-key")
        if not isinstance(host, dict) or set(host) != {"key_id", "architecture", "topology_sha256", "st_version"}:
            raise Refused("unenrolled-canary")
        pub = trust / "keys" / (key_id + ".pem")
        if sha256(pub) != key["public_key_sha256"]:
            raise Refused("public-key-digest-mismatch")
        # A dedicated Ed25519 public key, never the apt signing key.
        der = subprocess.run(["openssl", "pkey", "-pubin", "-in", str(pub), "-outform", "DER"],
                             check=True, capture_output=True).stdout
        if len(der) != 44 or not der.startswith(bytes.fromhex("302a300506032b6570032100")):
            raise Refused("not-ed25519")
        with tempfile.TemporaryDirectory() as temporary:
            data, sig = pathlib.Path(temporary) / "receipt", pathlib.Path(temporary) / "signature"
            data.write_bytes(raw)
            sig.write_bytes(signature)
            result = subprocess.run(["openssl", "pkeyutl", "-verify", "-pubin", "-inkey", str(pub),
                                     "-rawin", "-in", str(data), "-sigfile", str(sig)],
                                    capture_output=True, check=False)
        if result.returncode:
            raise Refused("invalid-signature")
        if receipt["contract"] != CONTRACT or host["key_id"] != key_id:
            raise Refused("contract-or-key-mismatch")
        for name in ("topology_sha256", "st_version"):
            if not isinstance(host[name], str) or not host[name] or receipt[name] != host[name]:
                raise Refused("canary-policy-mismatch")
        if not HEX.fullmatch(host["topology_sha256"]):
            raise Refused("invalid-topology-digest")
        if receipt["outcome"] != "PASS" or receipt["checks"] != dict.fromkeys(CHECKS, True):
            raise Refused("incomplete-smoke")
        # bool equality alone would accept 1 as True in Python.
        if any(v is not True for v in receipt["checks"].values()):
            raise Refused("incomplete-smoke")
        if not isinstance(receipt["evidence_sha256"], str) or not HEX.fullmatch(receipt["evidence_sha256"]):
            raise Refused("invalid-evidence-digest")
        supplied = receipt["candidate"]
        fields = {"package", "version", "architecture", "deb_sha256", "archive_sha256",
                  "repository", "tag", "commit"}
        if not isinstance(supplied, dict) or set(supplied) != fields:
            raise Refused("invalid-candidate")
        if any(supplied.get(k) != v for k, v in identity.items()):
            raise Refused("package-identity-mismatch")
        if host["architecture"] != identity["architecture"]:
            raise Refused("canary-architecture-mismatch")
        if supplied["repository"] != "scbrown/seeds" or supplied["tag"] != "seeds-ai-v" + identity["version"]:
            raise Refused("release-identity-mismatch")
        if not isinstance(supplied["commit"], str) or not re.fullmatch(r"[0-9a-f]{40}", supplied["commit"]):
            raise Refused("invalid-release-commit")
        if candidate is not None and supplied != candidate:
            raise Refused("candidate-mismatch")
        measured, expires = _time(receipt["measured_at"]), _time(receipt["expires_at"])
        now = now or dt.datetime.now(dt.timezone.utc)
        if measured > now or not measured < expires <= measured + dt.timedelta(hours=24):
            raise Refused("invalid-receipt-window")
        if promotion and now >= expires:
            raise Refused("expired-promotion-receipt")
        return supplied
    except (OSError, subprocess.SubprocessError, TypeError, KeyError) as error:
        raise Refused("unavailable-canary-evidence") from error


def promote(deb, destination, candidate, trust=DEFAULT_TRUST):
    """Copy tested bytes atomically, without rebuilding. Never overwrite a version."""
    # A no-op on an already admitted stable version is not a fresh promotion.
    verify(deb, trust, candidate=candidate, promotion=not destination.exists())
    if destination.exists():
        if sha256(destination) != candidate["deb_sha256"]:
            raise Refused("immutable-stable-package-mismatch")
        return None
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=destination.parent) as temporary:
        staged = pathlib.Path(temporary) / destination.name
        shutil.copyfile(deb, staged)
        if sha256(staged) != candidate["deb_sha256"]:
            raise Refused("candidate-changed-during-copy")
        # link is atomic and refuses a concurrent replacement of an immutable name.
        os.link(staged, destination)
    return destination
