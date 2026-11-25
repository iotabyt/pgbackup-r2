#!/bin/sh
# Installs pgb into ~/.local/bin, from the latest release:
#   curl -fsSL https://pgbackup-r2.iotabyt.com/install.sh | sh
# A specific release:
#   curl -fsSL https://pgbackup-r2.iotabyt.com/install.sh | PGB_VERSION=v0.4.2 sh
# It never edits your shell's startup files. If ~/.local/bin isn't on your PATH, it prints
# the line to add for your shell ($SHELL). pgb itself runs with bash, from any shell.
set -eu

repo=iotabyt/pgbackup-r2
api=https://api.github.com/repos/$repo
dir="$HOME/.local/bin"

die() {
  printf 'pgb installer: %s\n' "$*" >&2
  exit 1
}

# pgb comes from a release tag, never from main: main can be mid-change.
version=${PGB_VERSION:-}
if [ -z "$version" ]; then
  # The latest published release, or else the highest vX.Y.Z tag.
  version=$(curl -fsSL "$api/releases/latest" 2>/dev/null |
    sed -n 's/^ *"tag_name": *"\([^"]*\)".*/\1/p' | head -n 1) || true
  if [ -z "$version" ]; then
    version=$(curl -fsSL "$api/tags?per_page=100" 2>/dev/null |
      sed -n 's/^ *"name": *"\(v[0-9][0-9]*\.[0-9][0-9]*\.[0-9][0-9]*\)".*/\1/p' |
      sort -t. -k1.2,1n -k2,2n -k3,3n | tail -n 1) || true
  fi
  [ -n "$version" ] || die "couldn't find the latest pgb release: GitHub's API didn't answer.
  Check your connection and try again (the API allows 60 requests an hour per address),
  or name a release: curl -fsSL https://pgbackup-r2.iotabyt.com/install.sh | PGB_VERSION=v0.4.2 sh
  Releases: https://github.com/$repo/tags"
fi
url=${PGB_URL:-https://raw.githubusercontent.com/$repo/$version/scripts/pgb}

mkdir -p "$dir"
tmp="$dir/.pgb.download.$$"
trap 'rm -f "$tmp"' EXIT
curl -fsSL "$url" -o "$tmp" || die "couldn't download pgb $version from $url
  Is $version a release? See https://github.com/$repo/tags"
chmod +x "$tmp"
mv "$tmp" "$dir/pgb"
echo "Installed pgb $version to $dir/pgb"

command -v bash >/dev/null 2>&1 || echo "Note: pgb needs bash, which isn't installed." >&2
command -v docker >/dev/null 2>&1 || echo "Note: pgb needs Docker: https://docs.docker.com/get-docker/" >&2

case ":$PATH:" in
*":$dir:"* | *":$HOME/.local/bin/:"*)
  echo "Run: pgb help"
  exit 0
  ;;
esac

# shellcheck disable=SC2016 # the $HOME and $PATH below are for the user's shell to expand
case "$(basename "${SHELL:-sh}")" in
bash)
  # macOS terminals start login shells, which read ~/.bash_profile rather than ~/.bashrc.
  rc=.bashrc
  if [ "$(uname -s)" = Darwin ]; then rc=.bash_profile; fi
  line='echo '\''export PATH="$HOME/.local/bin:$PATH"'\'' >> ~/'"$rc"' && source ~/'"$rc"
  ;;
zsh) line='echo '\''export PATH="$HOME/.local/bin:$PATH"'\'' >> ~/.zshrc && source ~/.zshrc' ;;
fish) line='fish_add_path ~/.local/bin' ;;
*) line='export PATH="$HOME/.local/bin:$PATH"   # add this to your shell'\''s startup file' ;;
esac
echo
echo "$dir isn't on your PATH yet. Run this once:"
echo
echo "  $line"
echo
echo "Then: pgb help"
