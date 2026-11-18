#!/bin/sh
# Installs pgb into ~/.local/bin:
#   curl -fsSL https://raw.githubusercontent.com/iotabyt/pgbackup-r2/main/scripts/install.sh | sh
# It never edits your shell's startup files. If ~/.local/bin isn't on your PATH, it prints
# the line to add for your shell ($SHELL). pgb itself runs with bash, from any shell.
set -eu

url=${PGB_URL:-https://raw.githubusercontent.com/iotabyt/pgbackup-r2/main/scripts/pgb}
dir="$HOME/.local/bin"

mkdir -p "$dir"
tmp="$dir/.pgb.download.$$"
trap 'rm -f "$tmp"' EXIT
curl -fsSL "$url" -o "$tmp"
chmod +x "$tmp"
mv "$tmp" "$dir/pgb"
echo "Installed pgb to $dir/pgb"

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
