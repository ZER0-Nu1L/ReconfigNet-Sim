#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 1 ]]; then
    echo "usage: $0 /absolute/path/to/agent.json [agent_main options]" >&2
    exit 2
fi

config_path=$1
shift

script_dir=$(cd "$(dirname "$0")" && pwd)
repository_dir=$(cd "$script_dir/../.." && pwd)
sde_install=${SDE_INSTALL:-/root/bf-sde-9.3.1/install}
python_runtime=${OCS_PYTHON_RUNTIME:-/root/wcb/ocs-agent-python}

export SDE_INSTALL="$sde_install"
export PYTHONPATH="$python_runtime:$repository_dir/ocs.agent:$sde_install/lib/python2.7/site-packages:$sde_install/lib/python2.7/site-packages/tofino${PYTHONPATH:+:$PYTHONPATH}"

exec python3 -S "$script_dir/agent_main.py" --config "$config_path" "$@"
