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
