#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 1 ]]; then
    echo "usage: $0 /absolute/path/to/agent.json [launcher options]" >&2
    exit 2
fi

config_path=$1
shift

script_dir=$(cd "$(dirname "$0")" && pwd)
repository_dir=$(cd "$script_dir/../../.." && pwd)
if [[ -z "${SDE_INSTALL:-}" ]]; then
    echo "SDE_INSTALL must point to the BF-SDE install directory" >&2
    exit 2
fi

sde_install="$SDE_INSTALL"
python_runtime=${OCS_PYTHON_RUNTIME:-"$repository_dir/agent/python"}

export SDE_INSTALL="$sde_install"
export PYTHONPATH="$python_runtime:$repository_dir/agent/python:$sde_install/lib/python2.7/site-packages:$sde_install/lib/python2.7/site-packages/tofino${PYTHONPATH:+:$PYTHONPATH}"

exec python3 -S "$script_dir/launcher.py" --config "$config_path" "$@"
