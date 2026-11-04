#!/usr/bin/env bash
# Explicitly admitted DISPOSABLE container only; never invoke on a host.
# site must contain a fixture repository with OLD and NEW package revisions.
# Usage: disposable-lifecycle.sh SITE PACKAGE OLD NEW COMMAND [ALIAS]
set -euo pipefail
if [[ ${APT_LIFECYCLE_DISPOSABLE:-} != 1 || ! -f /.dockerenv || $(id -u) != 0 ]]; then
  echo 'REFUSED: requires an explicitly admitted disposable root container' >&2
  exit 2
fi
[[ $# -ge 5 && $# -le 6 ]] || { echo 'usage: SITE PACKAGE OLD NEW COMMAND [ALIAS]' >&2; exit 2; }
site=$(realpath "$1"); package=$2; old=$3; new=$4; command=$5; alias=${6:-}
[[ $package =~ ^[a-z0-9][a-z0-9.+-]*$ && $command =~ ^[a-z0-9][a-z0-9.+-]*$ ]]
[[ -z $alias || $alias =~ ^[a-z0-9][a-z0-9.+-]*$ ]]
dpkg --compare-versions "$new" gt "$old"
# The fixture is signed with a throwaway key; never mount a publication key.
[[ -f $site/fixture.gpg && -f $site/dists/stable/InRelease ]]
install -m 0644 "$site/fixture.gpg" /etc/apt/keyrings/stack-fixture.gpg
printf 'deb [signed-by=/etc/apt/keyrings/stack-fixture.gpg] file://%s stable main\n' "$site" > /etc/apt/sources.list.d/stack-fixture.list
apt-get update
version() { dpkg-query -W -f='${Version}' "$package"; }
owned() {
  [[ -z $(dpkg-query -W -f='${Conffiles}' "$package") ]]
  if [[ $command == dp ]]; then /usr/bin/dp version; else "/usr/bin/$command" --version; fi
  [[ $(dpkg-query -S "/usr/bin/$command" | cut -d: -f1) == "$package" ]]
  if [[ -n $alias ]]; then
    [[ $(dpkg-query -S "/usr/bin/$alias" | cut -d: -f1) == "$package" ]]
    "/usr/bin/$alias" --version
    if [[ -L /usr/bin/$alias ]]; then
      [[ $(readlink -f "/usr/bin/$alias") == "/usr/bin/$command" ]]
    else
      [[ $package == tambo && $alias == caboodle && -x /usr/bin/caboodle ]]
    fi
  fi
}
apt-get -y install "$package=$old"
[[ $(version) == "$old" ]]; owned
# A user-owned PATH shadow is deliberately preserved and reported.
userbin=$(mktemp -d)
printf '#!/bin/sh\nprintf "user shadow\\n"\n' > "$userbin/$command"
chmod +x "$userbin/$command"
[[ $(PATH="$userbin:/usr/bin:/bin" command -v "$command") == "$userbin/$command" ]]
state="$HOME/.config/stack-lifecycle/$package"
mkdir -p "$(dirname "$state")"; printf 'user-owned state\n' > "$state"
apt-mark hold "$package"
apt-get -y upgrade
[[ $(version) == "$old" ]]
apt-mark unhold "$package"
apt-get -y install "$package=$new"
[[ $(version) == "$new" ]]; owned
apt-get -y --allow-downgrades install "$package=$old"
[[ $(version) == "$old" ]]; owned
apt-get -y install "$package=$new"
[[ $(version) == "$new" ]]; owned
apt-get -y remove "$package"
[[ ! -e /usr/bin/$command && ! -L /usr/bin/$command ]]
if [[ -n $alias ]]; then [[ ! -e /usr/bin/$alias && ! -L /usr/bin/$alias ]]; fi
apt-get -y install "$package=$new"
owned
apt-get -y purge "$package"
[[ ! -e /usr/bin/$command && ! -L /usr/bin/$command ]]
[[ $(cat "$state") == 'user-owned state' ]]
[[ -x $userbin/$command ]]
echo "PASS: $package $old -> held -> $new -> $old -> $new -> remove -> reinstall -> purge; ownership, aliases, user state and PATH shadow checked"
