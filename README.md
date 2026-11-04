# scbrown apt repository

Signed Debian packages for the Quipu tool stack: CABOODLE, Quipu, Bobbin,
Yupana, Seeds and Desire Path. Each package repackages the exact binaries a
tool's GitHub release published, after verifying them against that release's
own checksums. Nothing is rebuilt from source here.

## Install

```bash
sudo install -d -m 0755 /etc/apt/keyrings
curl -fsSL https://scbrown.github.io/apt/key.gpg | sudo tee /etc/apt/keyrings/scbrown-stack.gpg >/dev/null
echo "deb [signed-by=/etc/apt/keyrings/scbrown-stack.gpg] https://scbrown.github.io/apt stable main" \
  | sudo tee /etc/apt/sources.list.d/scbrown-stack.list
sudo apt update
sudo apt install caboodle
```

Check the key fingerprint before trusting it:
`gpg --show-keys /etc/apt/keyrings/scbrown-stack.gpg` must print
`AB5625C5612F643BF76464DF0DC99924994EC959`.

## Channels and pinning

| Want | Command |
|---|---|
| Every release (default) | the `stable` line above |
| Prereleases, where a tool publishes them | replace `stable` with `nightly` |
| Stay on one version | `sudo apt install <tool>=<version>` then `sudo apt-mark hold <tool>` |
| Roll back | `sudo apt install <tool>=<previous version>` |

`apt-cache policy <tool>` shows the installed version, its channel and every
version still in the pool.

Architectures: amd64 for every package; arm64 where the tool publishes a Linux
arm64 release (Quipu, Bobbin, Seeds, Desire Path).

## How it publishes

`.github/workflows/publish.yml` runs every 30 minutes and on dispatch. It finds
new releases listed in `tools.json`, verifies and repackages them, regenerates
the indexes and signs `InRelease` and `Release.gpg` with the repository key. A
refused checksum fails the run and publishes nothing for that tool.

## Runtime requirements and package ownership

Packaging revisions preserve existing pool files: for example, `1.0-1` adds
metadata without replacing the published `1.0` binary package. The builder
inspects each checksum-verified ELF for its architecture, interpreter,
`DT_NEEDED` libraries and version requirements without running it. It derives
`libc6` from the highest required GLIBC version and maps the currently qualified
GCC/C++ symbol families to `libgcc-s1 (>= 4.2)` and `libstdc++6 (>= 4.1.1)`.
Unknown libraries or symbol families refuse that build. Static executables do
not acquire an invented libc dependency. Building requires GNU `readelf` from
`binutils` as well as `dpkg-deb`.

This covers directly linked core libraries. Bobbin and Quipu can load ONNX at
runtime, which does not appear in `DT_NEEDED`; compatible ONNX versions, model
assets and optional integrations still need separate qualification. Core
metadata does not prove that those features work on every Debian or Ubuntu
release. In particular, an executable requiring GLIBC 2.39 cannot run on a
system providing an older libc merely because its architecture matches.

Packages install executables and relative aliases in `/usr/bin` and documents
in `/usr/share/doc/<package>`. Release documents alongside the executables are
copied unchanged when present. `README.package` records the release checksum
and missing-document limitations; it does not substitute a current source-tree
license for an older release. No service, state directory, conffile or maintainer
script is installed. Installation, upgrade and removal do not activate services
or delete application state in the user's home directory.

APT owns the installed executable and its aliases (`qp`, `hank`, and the Tambo
`caboodle` command). Use APT to update, pin, downgrade or remove those packages.
Check `command -v <command>` and `dpkg-query -S <resolved-path>` when an older
user-installed binary shadows `/usr/bin`; package installation does not delete
that user file or rewrite PATH. Tambo's APT ownership checks delegate updates to
the package manager rather than replacing a package-owned executable.

The existing CI transition fixture uses a private dpkg root with the runner's
installed dependency metadata. It checks package ownership and alias transitions;
it is not a clean target-distribution lifecycle or ONNX compatibility proof.

`tests/disposable-lifecycle.sh` is a separate opt-in fixture for an explicitly
admitted disposable root container. It requires a local fixture repository,
old/new revisions and a throwaway signing key. It exercises actual APT hold,
upgrade, downgrade, remove, reinstall and purge, with dpkg ownership, alias,
user-state and PATH-shadow checks. It is not scheduled by CI: builder admission
and target/architecture coverage must be recorded before execution.

To prepare the fixture without installing anything, build candidates into a
private pool, then run `python3 tests/prepare-lifecycle-fixture.py --pool <pool>
--site <new-directory>`. It verifies the published InRelease signature, signed
index hashes and old DEB hashes, checks that each candidate sorts above its
baseline, rejects maintainer scripts, and signs the private fixture using a
throwaway key. The key is deleted after signing. No production signing material
is used. `tests/lifecycle-workflow.example.yml` is the concrete Debian 13 and
Ubuntu 24.04 amd64 proposal, outside the scheduled workflows until builder
scope is admitted. ARM runtime qualification remains separate.
