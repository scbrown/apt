# Preserve published stable packages during enrollment

The terminal recording shows a synthetic signed stable repository containing
Seeds 0.7.2, a new unsigned Seeds 0.7.1 candidate, and an unrelated package.
The original gate removes both Seeds versions with empty enrollment. The
repair retains only the exact published 0.7.2 bytes, excludes 0.7.1 and keeps
the unrelated package. A changed published package aborts before any metadata
rewrite. Exit 3 means exclusions were safely indexed; exit 2 means refusal
before writing metadata.

Before: original gate commit `00727f4372bf773e730610260b33ecfe58f9a98d`.
After: the exact source SHA256 values are printed in the recording, which is
retained with the repair commit. The baseline and pool bytes are identical;
the head additionally enables its explicit `retain_published_seeds` policy
flag. Version labels and signing keys are disposable fixtures. This proves
source behavior, not deployment, key enrollment or real canary acceptance.

Repeat from the repository root (Python 3, git, dpkg-deb, OpenSSL and GnuPG):

```sh
PYTHONDONTWRITEBYTECODE=1 python3 demos/stable-retention/run.py
```

[Output/transcript](output.log) and [timing](timing.log) form a replayable
terminal recording. Download both into one directory, then run:

```sh
scriptreplay --log-out output.log --log-timing timing.log
```

Playback was verified with util-linux scriptreplay. No inline player is
provided. These small sanitized artifacts remain in Git with the source
change; there is no external upload or separate retention dependency.
