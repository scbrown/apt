# Runtime dependency metadata demonstration

Trigger: repackage the same locally compiled, dynamically linked C executable
from a synthetic checksum-verified archive. No release executable is run.

On base `cc6d711c955091d38e8eee6a9709829958017e7a`, the resulting `.deb` has no
`Depends`. On this candidate, `dpkg-deb -f` reports `libc6 (>= 2.34)` on the
recording machine. Both extracted executables exactly match the input bytes.
The floor depends on the compiler/sysroot used to reproduce the fixture.

Run from the repository root:

```sh
python3 tests/demo_runtime_depends.py
scriptreplay --log-out docs/demos/runtime-depends/output.log \
  --log-timing docs/demos/runtime-depends/timing.log
```

The small output/timing pair is retained here with the change. It is a terminal
recording, with a readable transcript in `output.log`; no inline video player is
claimed. The recording is refreshed when the candidate builder changes.
This shows source packaging behavior, not deployment or clean-distro acceptance.
