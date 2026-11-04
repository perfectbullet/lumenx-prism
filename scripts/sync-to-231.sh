#!/usr/bin/env bash
set -euo pipefail
project_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
ssh_target="${PRISM_SSH_TARGET:-zenking@192.168.8.231}"
remote_dir="${PRISM_REMOTE_DIR:-/data/aigc/lumenx-prism}"
local_python="${PRISM_LOCAL_PYTHON:-/home/zj/miniconda3/envs/lumenx_prism/bin/python}"
dry_run=false
sync_media=true
for arg in "$@"; do
  case "$arg" in
    --dry-run) dry_run=true ;;
    --code-only) sync_media=false ;;
    -h|--help) echo "Usage: $0 [--dry-run] [--code-only]"; echo "Syncs code and .env; media is included unless --code-only. Does not install or restart."; exit 0 ;;
    *) echo "Unknown option: $arg" >&2; exit 2 ;;
  esac
done
[[ "$remote_dir" == /* && "$ssh_target" != -* ]] || { echo "Invalid remote target" >&2; exit 2; }
[[ -f "$project_root/.env" ]] || { echo "Missing Prism .env" >&2; exit 1; }
printf -v remote_q '%q' "$remote_dir"
if ! $dry_run; then
  ssh "$ssh_target" "umask 077; mkdir -p $remote_q/run/env-backups; if [ -f $remote_q/.env ]; then cp -p $remote_q/.env $remote_q/run/env-backups/.env.\$(date +%Y%m%d-%H%M%S-%N); chmod 600 $remote_q/run/env-backups/.env.*; fi"
fi
options=(-az --protect-args --itemize-changes --exclude=__pycache__/ --exclude='*.egg-info/' --exclude=mongo-init.js --exclude=.pytest_cache/ --exclude='*.pyc' --exclude=docs/agents/ --exclude=comfyui-qwen-image21/)
if $dry_run; then options+=(--dry-run); fi
paths=()
for name in src scripts tests workflows deploy pyproject.toml requirements.txt requirements-dev.txt README.md AGENTS.md .gitignore .env.example .env; do
  [[ ! -e "$project_root/$name" ]] || paths+=("$project_root/$name")
done
rsync "${options[@]}" "${paths[@]}" "$ssh_target:$remote_dir/"
for document in video-production.md image-polish.md script-planning.md comfyui-control.md work-library.md work-production.md; do
  if [[ -f "$project_root/docs/$document" ]]; then
    if ! $dry_run; then ssh "$ssh_target" "mkdir -p $remote_q/docs"; fi
    rsync "${options[@]}" "$project_root/docs/$document" "$ssh_target:$remote_dir/docs/"
  fi
done
if $sync_media; then
  media_source="$("$local_python" - "$project_root" <<'PY'
from pathlib import Path
import sys
from dotenv import dotenv_values
root=Path(sys.argv[1]);value=dotenv_values(root/'.env').get('PRISM_MEDIA_ROOT') or 'data/media'
path=Path(value);print(path.resolve() if path.is_absolute() else (root/path).resolve())
PY
)"
  if [[ -d "$media_source" ]]; then
    if ! $dry_run; then ssh "$ssh_target" "mkdir -p $remote_q/data/media"; fi
    media_options=(-az --protect-args --itemize-changes)
    if $dry_run; then media_options+=(--dry-run); fi
    rsync "${media_options[@]}" "$media_source/" "$ssh_target:$remote_dir/data/media/"
  fi
fi
if ! $dry_run; then
  ssh "$ssh_target" "chmod 600 $remote_q/.env; chmod +x $remote_q/scripts/prismctl.sh $remote_q/scripts/sync-to-231.sh"
fi
echo "Prism sync finished. No remote files deleted; dependencies and processes were not changed."
