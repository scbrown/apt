The same synthetic standard-library Camayoc release is passed to the base and
candidate package builder. Base b5ae75fa2cb22e6d04559017e6511017489ac594 refuses
because it expects ELF binaries. Candidate builder blob SHA256
3d5e2e00b53465003a6e0b94e77f78796e59131317b010cbc084ec509251c2a5 builds a Debian
package with Python 3.10 dependency and isolated cy/camayoc entrypoints.
This is an offline fixture, not an installation or publisher acceptance.

[Transcript](camayoc-package.log) and [timing](camayoc-package.time) are retained
in this existing demos directory with the source. Replay from the repository:
`scriptreplay --log-out demos/camayoc-package.log --log-timing demos/camayoc-package.time`.
Reproduce: `bash demos/record-camayoc-package.sh`. Terminal replay requires the
util-linux tools; no inline video playback is claimed.
