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
artifact_root=$(cd "$artifact_root" && pwd)
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

check_runtime_processes() {
    local phase=$1
    if ! kill -0 "$model_pid" 2>/dev/null; then
        echo "Tofino model exited $phase" >&2
        return 1
    fi
    if ! kill -0 "$switchd_pid" 2>/dev/null; then
        echo "bf_switchd exited $phase" >&2
        return 1
    fi
}

wait_for_tcp_port() {
    local port=$1
    local description=$2
    local timeout=$3
    local deadline=$((SECONDS + timeout))

    until (: >/dev/tcp/127.0.0.1/"$port") 2>/dev/null; do
        check_runtime_processes "while waiting for $description" || return 1
        if ((SECONDS >= deadline)); then
            echo "Timed out waiting for $description on port $port" >&2
            return 1
        fi
        sleep 2
    done
}

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
marker_file="$artifact_root/bfrt-initialize.marker"
rm -f -- "$marker_file"
runtime_marker_file=/tmp/ocs-bfrt-initialize.marker
rm -f -- "$runtime_marker_file"

setsid "$SDE/run_tofino_model.sh" \
    -p "$program_name" \
    -f "$ports_file" \
    --arch tofino \
    --log-dir "$artifact_root/model-runtime" \
    >"$artifact_root/model.log" 2>&1 &
model_pid=$!

sleep 2
kill -0 "$model_pid"

(
    cd "$build_root"
    exec setsid "$SDE/run_switchd.sh" \
        -p "$program_name" \
        --arch tofino \
        --server-listen-local-only \
        -C
) >"$artifact_root/switchd.log" 2>&1 &
switchd_pid=$!

wait_for_tcp_port \
    7777 \
    "bf_switchd status server" \
    "${SWITCHD_READY_TIMEOUT:-180}"
wait_for_tcp_port \
    50052 \
    "BF Runtime gRPC server" \
    "${BFRT_READY_TIMEOUT:-180}"

sleep 5
check_runtime_processes "after the runtime ports became ready"

config_file="$repository_root/targets/tofino/runtime/config/device-profile.example.json"
bootstrap_file="$build_root/initialize-bfrt.py"
# bf_switchd is launched through run_switchd.sh, which deliberately forwards
# only its own SDE variables through sudo.  Set the CI-only paths inside the
# embedded Python process instead of assuming the client's environment is
# inherited by that process.
printf '%s\n' \
    'import os' \
    "os.environ['OCS_CONFIG_FILE'] = '$config_file'" \
    "os.environ['OCS_NET_CTRL_DIR'] = '$repository_root/targets/tofino/runtime'" \
    "os.environ['OCS_BFRT_INIT_MARKER'] = '$runtime_marker_file'" \
    "_ocs_script = '$repository_root/targets/tofino/runtime/initialize_dataplane.py'" \
    "with open(_ocs_script, 'rb') as _ocs_source:" \
    "    exec(compile(_ocs_source.read(), _ocs_script, 'exec'), globals(), globals())" \
    >"$bootstrap_file"

command_file="$build_root/initialize-bfrt.cli"
printf 'bfrt_python %s\n' \
    "$bootstrap_file" \
    >"$command_file"

drive_bfshell() {
    local deadline=$((SECONDS + ${BFRT_INIT_TIMEOUT:-180}))

    if ! cat "$command_file"; then
        echo "Failed to send the BFRT initialization command" >&2
        return 1
    fi
    # Keep stdin open while the BFRT Python command runs.  Sending exit
    # earlier can terminate bfshell before the initialization has completed.
    until [[ -s "$runtime_marker_file" ]]; do
        check_runtime_processes \
            "before BFRT initialization completed" || return 1
        if ((SECONDS >= deadline)); then
            echo "Timed out waiting for the BFRT initialization marker" >&2
            return 1
        fi
        sleep 1
    done
    printf 'exit\n'
}

set +e
drive_bfshell | (
    cd "$build_root"
    "$SDE/run_bfshell.sh" --status-port 7777
) 2>&1 | tee "$artifact_root/bfrt-initialize.log"
bfrt_pipeline_status=("${PIPESTATUS[@]}")
set -e

if ((bfrt_pipeline_status[0] != 0 ||
     bfrt_pipeline_status[1] != 0 ||
     bfrt_pipeline_status[2] != 0)); then
    printf 'BFRT initialization pipeline failed (driver=%d bfshell=%d tee=%d)\n' \
        "${bfrt_pipeline_status[0]}" \
        "${bfrt_pipeline_status[1]}" \
        "${bfrt_pipeline_status[2]}" >&2
    exit 1
fi

if [[ -s "$runtime_marker_file" ]]; then
    cp -- "$runtime_marker_file" "$marker_file"
fi
test -s "$marker_file"
grep -qx 'ocs-bfrt-initialized' "$marker_file"
grep -Fq 'Loading OCS profile' "$artifact_root/bfrt-initialize.log"
grep -Fq 'Initial OCS mapping' "$artifact_root/bfrt-initialize.log"

echo "Tofino model verification passed for $program_name."
