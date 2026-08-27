#!/usr/bin/env python3
"""Run real cached-sync writes across multiple reconciliation periods."""

from __future__ import print_function

import argparse
import json
import os
import sys
import time

import grpc


PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_DIR not in sys.path:
    sys.path.insert(0, PROJECT_DIR)

from ocs_agent.proto import ocs_operations_pb2, ocs_operations_pb2_grpc
from benchmarks.batch_latency import alternate_pi


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--target', default='127.0.0.1:9339')
    parser.add_argument('--duration', type=float, default=121)
    parser.add_argument('--interval', type=float, default=0.25)
    parser.add_argument('--timeout', type=float, default=10)
    args = parser.parse_args()

    channel = grpc.insecure_channel(args.target)
    stub = ocs_operations_pb2_grpc.OcsOperationsStub(channel)
    lease = stub.AcquireControl(
        ocs_operations_pb2.AcquireControlRequest(
            client_id='reconcile-endurance'), timeout=args.timeout)
    token = lease.lease_token
    revision = lease.revision
    metadata = (('x-ocs-control-lease', token),)
    current = list(stub.GetPermutation(
        ocs_operations_pb2.Empty(), timeout=args.timeout).permutation.pi)
    alternate = alternate_pi(current)
    initial_state = stub.GetRuntime(
        ocs_operations_pb2.Empty(), timeout=args.timeout).state.device_state
    deadline = time.monotonic() + args.duration
    next_renew = time.monotonic() + 10
    index = 0
    samples = []
    try:
        while time.monotonic() < deadline:
            if time.monotonic() >= next_renew:
                lease = stub.RenewControl(
                    ocs_operations_pb2.RenewControlRequest(
                        lease_token=token), timeout=args.timeout)
                next_renew = time.monotonic() + 10
            target = alternate if index % 2 == 0 else current
            request = ocs_operations_pb2.ApplyBatchRequest(
                strategy=ocs_operations_pb2.EXECUTION_STRATEGY_DELTA,
                transport=ocs_operations_pb2.TRANSPORT_NATIVE_BATCH,
                has_expected_revision=True,
                expected_revision=revision)
            request.permutation.pi.extend(target)
            reply = stub.ApplyBatch(
                request, timeout=args.timeout, metadata=metadata)
            revision = reply.state.revision
            samples.append({
                'precondition_readback_us':
                    reply.timing.precondition_readback_us,
                'readback_us': reply.timing.readback_us,
                'cache_precondition_us':
                    reply.timing.cache_precondition_us,
            })
            index += 1
            time.sleep(args.interval)
    finally:
        final_state = stub.GetRuntime(
            ocs_operations_pb2.Empty(), timeout=args.timeout).state.device_state
        stub.ReleaseControl(
            ocs_operations_pb2.ReleaseControlRequest(lease_token=token),
            timeout=args.timeout)
        channel.close()

    result = {
        'duration_seconds': args.duration,
        'interval_seconds': args.interval,
        'write_count': len(samples),
        'initial_device_state': {
            'cache_status': initial_state.cache_status,
            'generation': initial_state.generation,
            'last_reconcile_unix_ns': initial_state.last_reconcile_unix_ns,
            'drift_count': initial_state.drift_count,
        },
        'final_device_state': {
            'cache_status': final_state.cache_status,
            'generation': final_state.generation,
            'last_reconcile_unix_ns': final_state.last_reconcile_unix_ns,
            'drift_count': final_state.drift_count,
        },
        'zero_precondition_readback_count': sum(
            item['precondition_readback_us'] == 0 for item in samples),
        'positive_post_readback_count': sum(
            item['readback_us'] > 0 for item in samples),
        'max_precondition_readback_us': max(
            item['precondition_readback_us'] for item in samples),
        'min_post_readback_us': min(
            item['readback_us'] for item in samples),
    }
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == '__main__':
    main()
