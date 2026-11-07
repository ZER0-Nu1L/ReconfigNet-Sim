#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 || $1 != /* ]]; then
    echo "usage: OCS_GO_AGENT_BINARY=/absolute/path/ocs-go-agent $0 /absolute/path/agent.json" >&2
    exit 2
fi

: "${OCS_GO_AGENT_BINARY:?set OCS_GO_AGENT_BINARY to the built reusable Go Agent}"
if [[ "$OCS_GO_AGENT_BINARY" != /* || ! -x "$OCS_GO_AGENT_BINARY" ]]; then
    echo "OCS_GO_AGENT_BINARY must be an absolute executable path" >&2
    exit 2
fi

# The Go binary validates the explicit profile and takes the shared BFRT lock.
# It connects to an already running bf_switchd; no Python Worker or SDE import.
exec "$OCS_GO_AGENT_BINARY" --config "$1"
