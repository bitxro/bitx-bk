#!/usr/bin/env bash
set -Eeuo pipefail

source_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
target_dir=/opt/bitx-bk
config_dir=/etc/bitx-bk

[[ $EUID -eq 0 ]] || { printf 'Run as root (sudo ./install.sh).\n' >&2; exit 1; }
[[ -d "$source_dir/.git" && -f "$source_dir/bin/bitx-bk" ]] || {
 printf 'Run this installer from a complete bitx-bk Git clone.\n' >&2; exit 1;
}
bash -n "$source_dir/bin/bitx-bk"

if [[ "$source_dir" != "$target_dir" ]]; then
 if [[ -e "$target_dir" ]]; then
  printf '%s already exists. Run %s/deploy.sh for updates.\n' "$target_dir" "$target_dir" >&2
  exit 1
 fi
 cp -a "$source_dir" "$target_dir"
fi

chmod +x "$target_dir/bin/bitx-bk" "$target_dir/deploy.sh"
mkdir -p "$config_dir"
chmod 700 "$config_dir"
if [[ ! -e "$config_dir/bitx-bk.conf" ]]; then
 install -m 600 "$target_dir/config/bitx-bk.conf.example" "$config_dir/bitx-bk.conf"
fi
ln -sfn "$target_dir/bin/bitx-bk" /usr/local/bin/bitx-bk

printf 'Installed bitx-bk v%s in %s\n' "$("$target_dir/bin/bitx-bk" --version)" "$target_dir"
printf 'Configure the repository with: bitx-bk repo-config-sftp (or repo-config-local)\n'
if ! command -v restic >/dev/null; then
 printf 'Restic is not installed. Install it before configuring a repository.\n' >&2
fi
