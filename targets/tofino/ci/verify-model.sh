#!/usr/bin/env bash
# Compile and smoke-test the site-neutral Tofino pipeline in Open P4 Studio.

set -euo pipefail

run_model=true
if [[ $# -gt 2 ]]; then
    echo "Usage: $0 [ARTIFACT_ROOT] [--compile-only]" >&2
    exit 2
fi
if [[ $# -eq 2 ]]; then
    if [[ "$2" != "--compile-only" ]]; then
        echo "Usage: $0 [ARTIFACT_ROOT] [--compile-only]" >&2
        exit 2
    fi
    run_model=false
fi

: "${SDE:?SDE must point to the Open P4 Studio checkout}"
: "${SDE_INSTALL:?SDE_INSTALL must point to the Open P4 Studio install}"

repository_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)
artifact_root=${1:-/artifacts/tofino-model}
build_root=${BUILD_ROOT:-/work/reconfig-net-tofino}
build_dir="$build_root/ocs"
p4_path="$repository_root/targets/tofino/p4/ocs.p4"
ports_file="$repository_root/targets/tofino/ci/model-ports.json"
program_name=ocs

mkdir -p "$artifact_root" "$build_root"
rm -rf -- "$build_dir"
mkdir -p "$build_dir"

{
    echo "SDE=$SDE"
    echo "SDE_INSTALL=$SDE_INSTALL"
    echo "P4_PATH=$p4_path"
    echo "P4C=$SDE_INSTALL/bin/bf-p4c"
    "$SDE_INSTALL/bin/bf-p4c" --version || true
} >"$artifact_root/toolchain.txt" 2>&1

cmake \
    -S "$SDE/p4studio" \
    -B "$build_dir" \
    -DCMAKE_MODULE_PATH="$SDE/cmake" \
    -DCMAKE_INSTALL_PREFIX="$SDE_INSTALL" \
    -DP4C="$SDE_INSTALL/bin/bf-p4c" \
    -DP4_PATH="$p4_path" \
    -DP4_NAME="$program_name" \
    -DP4_LANG=p4-16 \
    -DTOFINO=ON \
    -DTOFINO2=OFF \
    -DTOFINO2M=OFF \
    -DTOFINO3=OFF \
    -DTHRIFT-DRIVER=OFF \
    -DWITHPD=OFF \
    2>&1 | tee "$artifact_root/cmake.log"

cmake --build "$build_dir" \
    --target install \
    --parallel "${BUILD_JOBS:-$(nproc)}" \
    2>&1 | tee "$artifact_root/build.log"

conf="$SDE_INSTALL/share/p4/targets/tofino/$program_name.conf"
runtime_dir="$SDE_INSTALL/share/tofinopd/$program_name"
test -f "$conf"
test -f "$runtime_dir/bf-rt.json"
find "$runtime_dir" -type f -name context.json -print -quit | grep -q .
find "$runtime_dir" -type f -name tofino.bin -print -quit | grep -q .

{
    printf '%s\n' "$conf"
    find "$runtime_dir" -type f \( -name '*.json' -o -name '*.conf' \)
} | sort -u | while IFS= read -r file; do
    relative=${file#"$SDE_INSTALL"/}
    printf '%s  %s\n' "$(sha256sum "$file" | awk '{print $1}')" "$relative"
done >"$artifact_root/generated.sha256"

PYTHONPATH="$repository_root/agent/python:$repository_root/targets/tofino/runtime:$repository_root" \
PYTHONDONTWRITEBYTECODE=1 \
    python3 -m unittest discover \
    -s "$repository_root/targets/tofino/runtime/tests" \
    -v 2>&1 | tee "$artifact_root/runtime-tests.log"

if [[ "$run_model" != true ]]; then
    echo "Tofino pipeline compilation and runtime tests passed."
    exit 0
fi

"$SDE_INSTALL/bin/veth_setup.sh" 128 \
    >"$artifact_root/veth-setup.log" 2>&1

model_pid=
switchd_pid=

cleanup() {
    local pid
    set +e
    for pid in "$switchd_pid" "$model_pid"; do
        if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
            kill -TERM -- "-$pid" 2>/dev/null || true
        fi
    done
    sleep 2
    for pid in "$switchd_pid" "$model_pid"; do
        if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
            kill -KILL -- "-$pid" 2>/dev/null || true
        fi
        if [[ -n "$pid" ]]; then
            wait "$pid" 2>/dev/null || true
        fi
    done
}
trap cleanup EXIT INT TERM

mkdir -p "$artifact_root/model-runtime"
setsid "$SDE/run_tofino_model.sh" \
    -p "$program_name" \
    -f "$ports_file" \
    --arch tofino \
    --log-dir "$artifact_root/model-runtime" \
    >"$artifact_root/model.log" 2>&1 &
model_pid=$!

sleep 2
kill -0 "$model_pid"

setsid "$SDE/run_switchd.sh" \
    -p "$program_name" \
    --arch tofino \
    --server-listen-local-only \
    -C \
    >"$artifact_root/switchd.log" 2>&1 &
switchd_pid=$!

deadline=$((SECONDS + ${SWITCHD_READY_TIMEOUT:-180}))
until (echo >/dev/tcp/127.0.0.1/7777) 2>/dev/null; do
    kill -0 "$model_pid" 2>/dev/null || {
        echo "Tofino model exited before switchd became ready" >&2
        exit 1
    }
    kill -0 "$switchd_pid" 2>/dev/null || {
        echo "bf_switchd exited before its status server became ready" >&2
        exit 1
    }
    if ((SECONDS >= deadline)); then
        echo "Timed out waiting for bf_switchd status port" >&2
        exit 1
    fi
    sleep 2
done

sleep 5
kill -0 "$model_pid"
kill -0 "$switchd_pid"

export OCS_CONFIG_FILE="$repository_root/targets/tofino/runtime/config/device-profile.example.json"
export OCS_NET_CTRL_DIR="$repository_root/targets/tofino/runtime"
(
    cd "$build_root"
    "$SDE/run_bfshell.sh" \
        -b "$repository_root/targets/tofino/runtime/initialize_dataplane.py" \
        --status-port 7777
) 2>&1 | tee "$artifact_root/bfrt-initialize.log"

echo "Tofino model verification passed for $program_name."
