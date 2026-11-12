#!/usr/bin/env bash
set -eu
scratch=$(mktemp -d)
trap 'rm -rf "$scratch"' EXIT
base=b5ae75fa2cb22e6d04559017e6511017489ac594
git show "$base":scripts/build_debs.py > "$scratch/base.py"
printf 'Same synthetic stdlib release, base builder %s\n' "$base"
python3 demos/camayoc-package.py "$scratch/base.py"
printf '\nCandidate builder SHA256 %s\n' "$(sha256sum scripts/build_debs.py | cut -d' ' -f1)"
python3 demos/camayoc-package.py scripts/build_debs.py
