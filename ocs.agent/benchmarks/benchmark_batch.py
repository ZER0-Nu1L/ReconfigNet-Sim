#!/usr/bin/env python3
"""Measure OcsOperations ApplyBatch latency and server-side timing.

This tool changes the active OCS mapping. Run it only against a test instance.
"""

from __future__ import print_function

import argparse
import collections
from concurrent import futures
import json
import math
import os
import sys
import time
import threading

import grpc


PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_DIR not in sys.path:
    sys.path.insert(0, PROJECT_DIR)


from api.proto import ocs_operations_pb2, ocs_operations_pb2_grpc


def monotonic_ns():
    try:
        return time.monotonic_ns()
    except AttributeError:
        return int(time.monotonic() * 1000000000)


def percentile(values, percent):
    if not values:
        return 0
    ordered = sorted(values)
    rank = int(math.ceil((percent / 100.0) * len(ordered))) - 1
    return ordered[max(0, min(rank, len(ordered) - 1))]


def validate_pi(mapping):
    if len(mapping) < 2 or len(mapping) % 2:
        raise ValueError('pi must have a positive even number of ports')
    if sorted(mapping) != list(range(1, len(mapping) + 1)):
        raise ValueError('pi must be a permutation of 1..N')
    for source, destination in enumerate(mapping, start=1):
        if source == destination or mapping[destination - 1] != source:
            raise ValueError('pi must contain symmetric non-self pairs')
    return mapping


def matching_permutations(port_count):
    if port_count < 2 or port_count % 2:
        raise ValueError('port_count must be a positive even integer')

    def pairings(remaining):
        if not remaining:
            yield []
            return
        first = remaining[0]
        for index in range(1, len(remaining)):
            second = remaining[index]
            rest = remaining[1:index] + remaining[index + 1:]
            for tail in pairings(rest):
                yield [(first, second)] + tail

    result = []
    for pairs in pairings(list(range(1, port_count + 1))):
        mapping = [0] * port_count
        for left, right in pairs:
            mapping[left - 1] = right
            mapping[right - 1] = left
        result.append(mapping)
    return result


def alternate_pi(current):
    if len(current) == 2:
        return list(current)
    pairs = []
    visited = set()
    for source, destination in enumerate(current, start=1):
        if source in visited:
            continue
        visited.add(source)
        visited.add(destination)
        pairs.append((source, destination))
    first_left, first_right = pairs[0]
    second_left, second_right = pairs[1]
    alternate = list(current)
    for left, right in (
            (first_left, second_left), (first_right, second_right)):
        alternate[left - 1] = right
        alternate[right - 1] = left
    return alternate


def request_for(mapping, strategy, transport, revision):
    strategy_value = {
        'FULL': ocs_operations_pb2.EXECUTION_STRATEGY_FULL,
        'DELTA': ocs_operations_pb2.EXECUTION_STRATEGY_DELTA,
    }[strategy]
    transport_value = {
        'SEQUENTIAL': ocs_operations_pb2.TRANSPORT_SEQUENTIAL,
        'NATIVE_BATCH': ocs_operations_pb2.TRANSPORT_NATIVE_BATCH,
    }[transport]
    request = ocs_operations_pb2.ApplyBatchRequest(
        strategy=strategy_value,
        transport=transport_value,
        has_expected_revision=True,
        expected_revision=revision)
    request.permutation.pi.extend(mapping)
    return request


def invoke(stub, mapping, strategy, transport, timeout, session):
    total_started_ns = monotonic_ns()
    with session['lock']:
        prepare_started_ns = monotonic_ns()
        request = request_for(
            mapping, strategy, transport, session['revision'])
        metadata = (('x-ocs-control-lease', session['token']),)
        prepare_us = (monotonic_ns() - prepare_started_ns) // 1000
        rpc_started_ns = monotonic_ns()
        response = stub.ApplyBatch(
            request, timeout=timeout, metadata=metadata)
        rpc_us = (monotonic_ns() - rpc_started_ns) // 1000
        session['revision'] = response.state.revision
    client_us = (monotonic_ns() - total_started_ns) // 1000
    return {
        'client_us': client_us,
        'client_prepare_us': prepare_us,
        'client_rpc_us': rpc_us,
        'server_us': response.timing.server_total_us,
        'queue_us': response.timing.queue_wait_us,
        'programming_us': response.timing.programming_total_us,
        'write_requests': response.timing.device_write_requests,
        'result': response.result,
    }


def run_strategy(target, strategy, transport, first_pi, second_pi,
                 warmup, iterations, concurrency, timeout):
    mappings = (first_pi, second_pi)
    channel = grpc.insecure_channel(target)
    try:
        stub = ocs_operations_pb2_grpc.OcsOperationsStub(channel)
        lease = stub.AcquireControl(
            ocs_operations_pb2.AcquireControlRequest(
                client_id='python-batch-benchmark'), timeout=timeout)
        session = {
            'lock': threading.Lock(),
            'token': lease.lease_token,
            'revision': lease.revision,
        }
        for index in range(warmup):
            invoke(stub, mappings[index % 2], strategy, transport,
                   timeout, session)
        invoke(stub, mappings[1], strategy, transport, timeout, session)

        requests = [mappings[index % 2]
            for index in range(iterations)
        ]
        started_ns = monotonic_ns()
        if concurrency == 1:
            results = [invoke(
                stub, mapping, strategy, transport, timeout, session)
                for mapping in requests]
        else:
            executor = futures.ThreadPoolExecutor(max_workers=concurrency)
            try:
                pending = [
                    executor.submit(
                        invoke, stub, mapping, strategy, transport,
                        timeout, session)
                    for mapping in requests
                ]
                results = [item.result() for item in pending]
            finally:
                executor.shutdown()
        elapsed_seconds = (monotonic_ns() - started_ns) / 1000000000.0
        stub.ReleaseControl(
            ocs_operations_pb2.ReleaseControlRequest(
                lease_token=session['token']), timeout=timeout)
    finally:
        channel.close()

    client_values = [item['client_us'] for item in results]
    prepare_values = [item['client_prepare_us'] for item in results]
    rpc_values = [item['client_rpc_us'] for item in results]
    server_values = [item['server_us'] for item in results]
    queue_values = [item['queue_us'] for item in results]
    programming_values = [item['programming_us'] for item in results]
    result_counts = collections.Counter(item['result'] for item in results)
    return {
        'strategy': strategy,
        'transport': transport,
        'iterations': iterations,
        'concurrency': concurrency,
        'throughput_ops_s': (
            iterations / elapsed_seconds if elapsed_seconds else 0),
        'results': dict(result_counts),
        'client_latency_us': summary(client_values),
        'client_prepare_us': summary(prepare_values),
        'client_rpc_us': summary(rpc_values),
        'server_total_us': summary(server_values),
        'queue_wait_us': summary(queue_values),
        'programming_total_us': summary(programming_values),
        'mean_device_write_requests': (
            sum(item['write_requests'] for item in results) /
            float(len(results))),
    }


def summary(values):
    return {
        'min': min(values),
        'mean': sum(values) / float(len(values)),
        'p50': percentile(values, 50),
        'p95': percentile(values, 95),
        'p99': percentile(values, 99),
        'max': max(values),
    }


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description='Benchmark Full/Delta OCS batch operations')
    parser.add_argument('--target', default='127.0.0.1:9339')
    parser.add_argument(
        '--strategy', choices=('full', 'delta', 'both'), default='both')
    parser.add_argument(
        '--transport', choices=('sequential', 'native-batch'),
        default='sequential')
    parser.add_argument('--warmup', type=int, default=4)
    parser.add_argument('--iterations', type=int, default=100)
    parser.add_argument('--concurrency', type=int, default=1)
    parser.add_argument('--timeout', type=float, default=10.0)
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    if args.warmup < 0 or args.iterations < 1 or args.concurrency < 1:
        raise SystemExit(
            'warmup must be >= 0; iterations and concurrency must be >= 1')

    channel = grpc.insecure_channel(args.target)
    try:
        stub = ocs_operations_pb2_grpc.OcsOperationsStub(channel)
        current = list(stub.GetPermutation(
            ocs_operations_pb2.Empty(), timeout=args.timeout).permutation.pi)
    finally:
        channel.close()
    validate_pi(current)
    alternate = alternate_pi(current)

    strategy_names = (
        ('FULL', 'DELTA') if args.strategy == 'both'
        else (args.strategy.upper(),))
    transport = args.transport.replace('-', '_').upper()
    output = {
        'target': args.target,
        'port_count': len(current),
        'initial_pi': current,
        'alternate_pi': alternate,
        'runs': [],
    }
    for strategy in strategy_names:
        output['runs'].append(run_strategy(
            args.target, strategy, transport, alternate, current,
            args.warmup, args.iterations, args.concurrency, args.timeout))
    print(json.dumps(output, indent=2, sort_keys=True))


if __name__ == '__main__':
    main()
