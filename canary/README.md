# Seeds canary admission

New **seeds stable** packages are copies of canary bytes, admitted only by an
Ops-signed receipt. Other tools and the nightly channel retain their behavior.
The scheduled, manual and repository-dispatch publisher share the same path:
build canary → verify/promote stable → independently verify the stable index.
An unapproved seeds deb is excluded from `Packages`, except for the explicit
pre-enrollment preservation described below.
Index exit 3 means a safe index was written with exclusions; the publisher can
sign that index for other tools and reports a failed run. Other index errors
stop signing/publication, including when an old index exists.

## Enrollment is not complete

`policy.json` deliberately enrolls **no keys or canaries**. No test key is
trusted by production. A dedicated Ops-owned Ed25519 attestation trust anchor
requires its own approval and reviewed public-key enrollment. Until then no
new Seeds version can be promoted to stable.

The explicit `retain_published_seeds: true` setting preserves availability of
**exact previously published bytes**, using the existing apt public key in
`key.gpg`. The indexer verifies the prior stable `InRelease` signature in an
isolated keyring, suite and validity dates, the signed SHA256/size of both
architecture indexes, and every retained Seeds package's path, size and SHA256.
It refuses missing, expired, tampered or changed baseline data **before writing
any index**. An arbitrary file in the pool is not a published baseline, and
retention never authorizes copying a new package into stable.

Retention is allowed only while both enrollment maps are empty. Activation
must explicitly set `retain_published_seeds` to false (or remove it), enroll
approved public keys and canaries, and provide receipts for the exact versions
that should remain available. Clearing or revoking enrollment after that does
not reactivate retention. Keep a known-good local rollback package; do not use
an ungated index as an automatic error fallback.

This source change does not authorize creating a key, running a canary,
removing host holds or publishing a new Seeds version. Runtime activation needs
the separate trust decision, named Ops owner, representative canary and rollback
packet. Other tools retain their publication path during that preparation.

## Trust policy and receipts

The enrolled public policy has these keys (the optional boolean
`retain_published_seeds` must be false when enrollment is present):

```json
{
  "contract": "seeds-canary-v1",
  "keys": {
    "approved-key-id": {"public_key_sha256": "<64 lowercase hex>"}
  },
  "canaries": {
    "approved-opaque-canary-id": {
      "key_id": "approved-key-id",
      "architecture": "amd64",
      "topology_sha256": "<64 lowercase hex>",
      "st_version": "<exact approved st version>"
    }
  }
}
```

The matching public key is `keys/approved-key-id.pem`. IDs are lowercase
letters/digits/hyphens, at most 64 characters. The pinned PEM hash and the
Ed25519 algorithm are verified. The apt repository signing key is separate and
must never be used for canary attestations. Enrollment covers only the named
architecture; an amd64 canary never approves arm64. Use opaque public IDs and
digests; do not commit internal hostnames, topology, credentials or board data.

For each candidate the builder emits `pool/canary/seeds/*.candidate.json`, with
actual package/version/architecture, deb SHA256, upstream archive SHA256,
repository (`intigraph/seeds`), tag and resolved full commit. The upstream archive
digest is checked against the release checksum again before promotion. The
tag is resolved through the commits API, not its potentially moving
`target_commitish` label. Promotion never rebuilds the tested deb.

Ops supplies `receipts/<deb-sha256>.json` and a 64-byte detached Ed25519 signature
`receipts/<deb-sha256>.sig`. Sign the **exact JSON bytes**, including any final
newline. Unknown/duplicate JSON fields or keys are refused. The receipt is:

```json
{
  "contract": "seeds-canary-v1",
  "key_id": "approved-key-id",
  "canary_id": "approved-opaque-canary-id",
  "topology_sha256": "<same approved digest>",
  "st_version": "<same exact approved version>",
  "measured_at": "2026-10-24T12:00:00Z",
  "expires_at": "2026-10-24T13:00:00Z",
  "candidate": {"package": "seeds", "version": "<candidate>",
    "architecture": "amd64", "deb_sha256": "<digest>",
    "archive_sha256": "<digest>", "repository": "intigraph/seeds",
    "tag": "seeds-ai-v<candidate>", "commit": "<full 40 hex>"},
  "checks": {"version": true, "show": true, "identity": true,
    "st_comment": true, "author_actor_readback": true,
    "rollback_ready": true, "hold_restored": true},
  "outcome": "PASS",
  "evidence_sha256": "<digest of the retained sanitized result>"
}
```

`measured_at` cannot be in the future. `expires_at` must be later and no more
than 24 hours after measurement; a new stable copy requires an unexpired
receipt. Already-admitted identical stable bytes need no renewed smoke every
30 minutes: their signature, contract and **current** canary/key/topology/st
policy are still checked on every index and no-op promotion. Changing policy
invalidates mismatched receipts, rather than silently carrying approval over.
Expiration never authorizes a new copy of an old stable version.

No private key is needed by the publisher. In Ops' separate signing environment,
the signature format is `openssl pkeyutl -sign -rawin -inkey PRIVATE_KEY -in
RECEIPT.json -out RECEIPT.sig`. This describes the format, not a key-generation
or canary-execution command. The signing environment must sign only its own
complete successful smoke result, never arbitrary caller-supplied PASS JSON.
Submit the public receipt/signature through the normal reviewed source lane;
an unauthenticated dispatch payload cannot enroll it.

## Canary execution contract (separate Ops lane)

One approved, representative canary host and one dedicated smoke item only.
Use the real crew transport, installed st and actor/principal arrangement;
do not weaken TLS/signing or copy another host's key to make a test pass.
Before a bounded package transaction, preserve and hash the exact previous
package/binary and hold state, and establish a named rollback. Coordinate the
package-manager lock with unattended upgrades through the sanctioned route.

Required positive sequence:

1. `sd --version` identifies exactly the tested candidate.
2. `sd show` of the dedicated smoke item succeeds.
3. `sd identity --json` satisfies that release's documented contract.
4. An actual `st work item comment` on that item succeeds, then a separate
   read-back confirms its unique nonce and expected author/actor.
5. The prior package is available for rollback; the canary hold is restored
   and verified before a PASS receipt is signed.

On any failure or UNKNOWN, do not sign PASS or promote. Restore the prior
package and hold, verify their effect, and record failure. A rollback failure
is an incident. An uncertain write result requires read-back before any retry;
do not replay it blindly or delete evidence. Retain raw evidence privately,
publish only sanitized result digests, and keep all writes within the one item.

Runtime acceptance requires the real 0.7.1 negative and the released compatible
version (0.8.0 superseded the proposed 0.7.2) positive
under this same smoke, plus one observed scheduled publisher run enforcing
admission. Synthetic fixtures below establish source behavior only. No host
runner, service restart, key creation or live smoke is performed by this PR.

## Reproduce the source demonstration

Run `python3 -m unittest discover -s tests -p 'test_*.py' -v`. It creates private real Debian packages
and throwaway Ed25519 keys, without network, installation, signing-key access or
board writes. The tests named
`test_071_preexisting_pool_bypass_excluded_other_tool_retained` and
`test_exact_signed_good_promotion_and_index` demonstrate the negative and
positive paths. Hosted CI explicitly runs the script.

Before this change, the same unsigned synthetic 0.7.1 package is indexed by
`index_repo.py` without a receipt; after it is excluded, while a signed fixture
0.7.2 is copied byte-identically and indexed. The version labels are fixtures,
not a claim that either real canary execution has passed. A signature attests
Ops' smoke: the publisher cannot independently reconstruct the board result.

The retention controls in `tests/test_retained_stable.py` use a real throwaway
GPG signing key and tiny Debian packages. With the same empty enrollment, the
original gate removes the signed existing Seeds version; the repaired gate
retains those exact bytes, excludes a new unsigned version and keeps an
unrelated tool. Wrong keys, altered indexes, missing/changed package bytes,
expired baselines and attempted promotion are independent negative controls.
These are synthetic source fixtures, not installed canary acceptance.
