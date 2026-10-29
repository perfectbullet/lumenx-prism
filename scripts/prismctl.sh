#!/usr/bin/env bash
set -euo pipefail
script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
prism_python="${PRISM_PYTHON:-/home/zenking/miniconda3/envs/lumenx_prism/bin/python}"
if [[ ! -x "$prism_python" ]]; then
  echo "Prism interpreter not found: $prism_python" >&2
  exit 1
fi
exec "$prism_python" "$script_dir/prismctl.py" "$@"
