#!/usr/bin/env bash
# End-to-end packaging checks over a sandboxed apt and a non-root dpkg root:
#   - quipu ships /usr/bin/qp -> quipu, yupana ships /usr/bin/hank -> yupana
#   - a host with the last real caboodle .deb (0.3.4, the published bytes)
#     moves to tambo through the transitional caboodle package, and the
#     caboodle command keeps working, now owned by tambo
# Usage: tests/e2e-transition.sh <site>   (site holds pool/ and dists/stable)
set -euo pipefail
site=$(realpath "$1")
work=$(mktemp -d); trap 'rm -rf "$work"' EXIT
root="$work/root"; apt_dir="$work/apt"

# The real caboodle 0.3.4 package as this repository published it.
old_url=https://scbrown.github.io/apt/pool/stable/caboodle/caboodle_0.3.4_amd64.deb
old_sha=62d2f0e11e2af01ad9a047bdcd0bb81db9ecb2c662e3635a2c18613ccac7bd9f
mkdir -p "$site/pool/stable/caboodle"
curl -fsSL --proto =https -o "$site/pool/stable/caboodle/caboodle_0.3.4_amd64.deb" "$old_url"
echo "$old_sha  $site/pool/stable/caboodle/caboodle_0.3.4_amd64.deb" | sha256sum -c -
python3 scripts/index_repo.py --root "$site" --channel stable

mkdir -p "$root"/var/lib/dpkg/{updates,info} \
  "$apt_dir"/{log,etc/apt/apt.conf.d,etc/apt/preferences.d,etc/apt/sources.list.d,state/lists/partial,cache/archives/partial}
touch "$root/var/lib/dpkg/status"
cat > "$apt_dir/apt.conf" <<CONF
Dir::Etc "$apt_dir/etc/apt";
Dir::State "$apt_dir/state";
Dir::State::status "$root/var/lib/dpkg/status";
Dir::Cache "$apt_dir/cache";
Dir::Log "$apt_dir/log";
APT::Sandbox::User "$(id -un)";
Debug::NoLocking "true";
DPkg::Options { "--root=$root"; "--force-not-root"; "--force-script-chrootless"; "--log=/dev/null"; };
CONF
export APT_CONFIG="$apt_dir/apt.conf"
# Signature handling is covered by the pipeline job; this checks package relations.
echo "deb [trusted=yes arch=amd64] file://$site stable main" > "$apt_dir/etc/apt/sources.list"
apt-get -q update >/dev/null
installed() { dpkg-query --admindir="$root/var/lib/dpkg" -W -f='${Version}' "$1" 2>/dev/null || true; }

apt-get -qy install caboodle=0.3.4 >/dev/null
test "$(installed caboodle)" = 0.3.4
want=$(sha256sum < "$root/usr/bin/caboodle")

# The upgrade pulls in a NEW package (tambo), so it is a full-upgrade, not a plain upgrade.
apt-get -qy full-upgrade >/dev/null
test "$(installed caboodle)" = 0.3.4-1
tambo=$(installed tambo); test -n "$tambo"
owner=$(dpkg-query --admindir="$root/var/lib/dpkg" -S /usr/bin/caboodle | cut -d: -f1)
test "$owner" = tambo || { echo "/usr/bin/caboodle owned by '$owner', not tambo"; exit 1; }
"$root/usr/bin/caboodle" --version >/dev/null
"$root/usr/bin/tambo" --version >/dev/null
echo "caboodle 0.3.4 -> transitional 0.3.4-1 + tambo $tambo; /usr/bin/caboodle owned by tambo"
[ "$want" = "$(sha256sum < "$root/usr/bin/caboodle")" ] && echo "(caboodle binary unchanged)" || echo "(caboodle binary updated)"

# The transitional package can go once nothing needs it; tambo keeps the command.
apt-get -qy remove caboodle >/dev/null
test "$(dpkg-query --admindir="$root/var/lib/dpkg" -W -f='${Status}' caboodle 2>/dev/null)" != "install ok installed"
test -x "$root/usr/bin/caboodle"

apt-get -qy install quipu yupana >/dev/null
test "$(readlink "$root/usr/bin/qp")" = quipu
test "$(readlink "$root/usr/bin/hank")" = yupana
"$root/usr/bin/qp" --version >/dev/null
"$root/usr/bin/hank" --version >/dev/null
echo "qp -> quipu $(installed quipu), hank -> yupana $(installed yupana)"
