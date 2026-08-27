#!/usr/bin/env python3
"""Compare HTTP compatibility and OcsOperations gRPC performance.

The benchmark changes the active OCS mapping. Use it only on a test instance.
HTTP uses one persistent HTTP/1.1 connection per concurrency slot. gRPC uses
one multiplexed channel, matching the normal usage model for each protocol.
"""

from __future__ import print_function

import argparse
import collections
from concurrent import futures
import http.client
import json
import os
import platform
import queue
import sys
import threading

import grpc


PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_DIR not in sys.path:
    sys.path.insert(0, PROJECT_DIR)


from ocs_agent.proto import ocs_operations_pb2, ocs_operations_pb2_grpc
from benchmarks.batch_latency import (
    alternate_pi,
    matching_permutations,
    monotonic_ns,
    summary,
    validate_pi,
)


def _strategy_value(strategy):
    return {
        'FULL': ocs_operations_pb2.EXECUTION_STRATEGY_FULL,
        'DELTA': ocs_operations_pb2.EXECUTION_STRATEGY_DELTA,
    }[strategy]


def _transport_value(transport):
    return {
        'SEQUENTIAL': ocs_operations_pb2.TRANSPORT_SEQUENTIAL,
        'NATIVE_BATCH': ocs_operations_pb2.TRANSPORT_NATIVE_BATCH,
    }[transport]


class GrpcClient(object):
    thread_safe = True

    def __init__(self, target, timeout, session=None):
        self.timeout = timeout
        self.channel = grpc.insecure_channel(target)
        self.stub = ocs_operations_pb2_grpc.OcsOperationsStub(self.channel)
        self.session = session

    def acquire(self):
        lease = self.stub.AcquireControl(
            ocs_operations_pb2.AcquireControlRequest(
                client_id='python-benchmark'), timeout=self.timeout)
        self.session.token = lease.lease_token
        self.session.revision = lease.revision

    def release(self):
        if self.session.token:
            self.stub.ReleaseControl(
                ocs_operations_pb2.ReleaseControlRequest(
                    lease_token=self.session.token), timeout=self.timeout)
            self.session.token = None

    def close(self):
        self.channel.close()

    def get_permutation(self):
        started_ns = monotonic_ns()
        response = self.stub.GetPermutation(
            ocs_operations_pb2.Empty(), timeout=self.timeout)
        latency_us = (monotonic_ns() - started_ns) // 1000
        return {
            'latency_us': latency_us,
            'pi': list(response.permutation.pi),
        }

    def get_runtime(self):
        return self.stub.GetRuntime(
            ocs_operations_pb2.Empty(), timeout=self.timeout)

    def apply(self, mapping, strategy, transport):
        total_started_ns = monotonic_ns()
        with self.session.lock:
            prepare_started_ns = monotonic_ns()
            request = ocs_operations_pb2.ApplyBatchRequest(
                strategy=_strategy_value(strategy),
                transport=_transport_value(transport),
                has_expected_revision=True,
                expected_revision=self.session.revision)
            request.permutation.pi.extend(mapping)
            metadata = (
                ('x-ocs-control-lease', self.session.token),)
            prepare_us = (monotonic_ns() - prepare_started_ns) // 1000
            rpc_started_ns = monotonic_ns()
            response = self.stub.ApplyBatch(
                request, timeout=self.timeout, metadata=metadata)
            rpc_us = (monotonic_ns() - rpc_started_ns) // 1000
            self.session.revision = response.state.revision
        latency_us = (monotonic_ns() - total_started_ns) // 1000
        timing = response.timing
        return {
            'latency_us': latency_us,
            'client_prepare_us': prepare_us,
            'client_rpc_us': rpc_us,
            'server_us': timing.server_total_us,
            'queue_us': timing.queue_wait_us,
            'programming_us': timing.programming_total_us,
            'write_requests': timing.device_write_requests,
            'result': response.result,
            'revision': response.state.revision,
        }


class HttpClient(object):
    thread_safe = False

    def __init__(self, target, timeout, session=None):
        host, port = target.rsplit(':', 1)
        self.connection = http.client.HTTPConnection(
            host, int(port), timeout=timeout)
        self.session = session

    def acquire(self):
        response = self._request('POST', '/ocs_control/acquire', {
            'client_id': 'python-benchmark',
        })
        self.session.token = response['lease_token']
        self.session.revision = response['revision']

    def release(self):
        if self.session.token:
            self._request('POST', '/ocs_control/release', {
                'lease_token': self.session.token,
            })
            self.session.token = None

    def close(self):
        self.connection.close()

    def _request(self, method, path, payload=None, extra_headers=None,
                 encoded_body=None):
        body = None
        headers = {}
        if encoded_body is not None:
            body = encoded_body
            headers['Content-Type'] = 'application/json'
        elif payload is not None:
            body = json.dumps(payload, separators=(',', ':'), sort_keys=True)
            headers['Content-Type'] = 'application/json'
        if extra_headers:
            headers.update(extra_headers)
        self.connection.request(method, path, body=body, headers=headers)
        response = self.connection.getresponse()
        raw = response.read()
        try:
            value = json.loads(raw.decode('utf-8'))
        except (ValueError, UnicodeDecodeError):
            raise RuntimeError(
                'HTTP {} returned non-JSON status {}'.format(
                    path, response.status))
        if response.status >= 400:
            raise RuntimeError(
                'HTTP {} failed with {}: {}'.format(
                    path, response.status, value))
        return value

    def get_permutation(self):
        started_ns = monotonic_ns()
        response = self._request('GET', '/ocs_mapping')
        latency_us = (monotonic_ns() - started_ns) // 1000
        return {
            'latency_us': latency_us,
            'pi': response['pi'],
        }

    def apply(self, mapping, strategy, transport):
        total_started_ns = monotonic_ns()
        with self.session.lock:
            prepare_started_ns = monotonic_ns()
            body = json.dumps({
                'new_pi': mapping, 'strategy': strategy,
                'transport': transport,
            }, separators=(',', ':'), sort_keys=True)
            headers = {
                'X-OCS-Control-Lease': self.session.token,
                'X-OCS-Expected-Revision': str(self.session.revision),
            }
            prepare_us = (monotonic_ns() - prepare_started_ns) // 1000
            rpc_started_ns = monotonic_ns()
            response = self._request(
                'POST', '/ocs_mapping', extra_headers=headers,
                encoded_body=body)
            rpc_us = (monotonic_ns() - rpc_started_ns) // 1000
            self.session.revision = response['revision']
        latency_us = (monotonic_ns() - total_started_ns) // 1000
        timing = response['timing']
        return {
            'latency_us': latency_us,
            'client_prepare_us': prepare_us,
            'client_rpc_us': rpc_us,
            'server_us': timing['server_total_us'],
            'queue_us': timing['queue_wait_us'],
            'programming_us': timing['programming_total_us'],
            'write_requests': timing['device_write_requests'],
            'result': response['result'],
            'revision': response['revision'],
        }


class ClientGroup(object):
    def __init__(self, protocol, target, timeout, concurrency):
        client_type = GrpcClient if protocol == 'grpc' else HttpClient
        client_count = 1 if client_type.thread_safe else concurrency
        self.session = type('ControlSession', (object,), {})()
        self.session.lock = threading.Lock()
        self.session.token = None
        self.session.revision = 0
        self.clients = [
            client_type(target, timeout, self.session)
            for _ in range(client_count)]
        self.thread_safe = client_type.thread_safe
        self.available = queue.Queue()
        for client in self.clients:
            self.available.put(client)
        self.clients[0].acquire()

    def close(self):
        self.clients[0].release()
        for client in self.clients:
            client.close()

    def call(self, method_name, *args):
        if self.thread_safe:
            return getattr(self.clients[0], method_name)(*args)
        client = self.available.get()
        try:
            return getattr(client, method_name)(*args)
        finally:
            self.available.put(client)


def _run_parallel(callables, concurrency):
    started_ns = monotonic_ns()
    if concurrency == 1:
        results = [item() for item in callables]
    else:
        executor = futures.ThreadPoolExecutor(max_workers=concurrency)
        try:
            pending = [executor.submit(item) for item in callables]
            results = [item.result() for item in pending]
        finally:
            executor.shutdown()
    elapsed_seconds = (monotonic_ns() - started_ns) / 1000000000.0
    return results, elapsed_seconds


def _base_result(protocol, operation, iterations, concurrency,
                 results, elapsed_seconds):
    latency_values = [item['latency_us'] for item in results]
    result = {
        'protocol': protocol,
        'operation': operation,
        'iterations': iterations,
        'concurrency': concurrency,
        'throughput_ops_s': (
            iterations / elapsed_seconds if elapsed_seconds else 0),
        'client_latency_us': summary(latency_values),
    }
    if 'result' in results[0]:
        server_values = [item['server_us'] for item in results]
        result.update({
            'results': dict(collections.Counter(
                item['result'] for item in results)),
            'server_total_us': summary(server_values),
            'client_prepare_us': summary([
                item['client_prepare_us'] for item in results]),
            'client_rpc_us': summary([
                item['client_rpc_us'] for item in results]),
            'protocol_and_wire_us': summary([
                max(0, item['latency_us'] - item['server_us'])
                for item in results
            ]),
            'queue_wait_us': summary([
                item['queue_us'] for item in results]),
            'programming_total_us': summary([
                item['programming_us'] for item in results]),
            'mean_device_write_requests': (
                sum(item['write_requests'] for item in results) /
                float(len(results))),
        })
    return result


def benchmark_read(group, protocol, warmup, iterations, concurrency):
    for _ in range(warmup):
        group.call('get_permutation')
    callables = [
        (lambda: group.call('get_permutation'))
        for _ in range(iterations)
    ]
    results, elapsed_seconds = _run_parallel(callables, concurrency)
    return _base_result(
        protocol, 'read', iterations, concurrency,
        results, elapsed_seconds)


def benchmark_apply(group, protocol, operation, current, alternate,
                    strategy, transport, warmup, iterations, concurrency):
    group.call('apply', current, strategy, transport)
    if operation == 'noop':
        for _ in range(warmup):
            group.call('apply', current, strategy, transport)
        mappings = [current for _ in range(iterations)]
    else:
        if concurrency == 1:
            candidates = (alternate, current)
        else:
            candidates = tuple(
                mapping for mapping in matching_permutations(len(current))
                if mapping != current)
        for index in range(warmup):
            group.call(
                'apply', candidates[index % len(candidates)],
                strategy, transport)
        group.call('apply', current, strategy, transport)
        mappings = [
            candidates[index % len(candidates)]
            for index in range(iterations)
        ]

    callables = [
        (lambda mapping=mapping: group.call(
            'apply', mapping, strategy, transport))
        for mapping in mappings
    ]
    results, elapsed_seconds = _run_parallel(callables, concurrency)
    if operation == 'write':
        contaminated = [
            item for item in results if item.get('result') != 'updated']
        if contaminated:
            raise RuntimeError(
                'Write benchmark was contaminated by {} non-update '
                'operations; reduce concurrency/iterations or use more '
                'ports'.format(len(contaminated)))
    group.call('apply', current, strategy, transport)
    result = _base_result(
        protocol, operation, iterations, concurrency,
        results, elapsed_seconds)
    result['strategy'] = strategy
    result['transport'] = transport
    return result


def _comparison_key(run):
    return (
        run['operation'], run.get('strategy'), run.get('transport'),
        run['iterations'], run['concurrency'])


def compare_protocols(runs):
    grouped = {}
    for run in runs:
        grouped.setdefault(_comparison_key(run), {})[run['protocol']] = run
    comparisons = []
    for key, values in sorted(grouped.items(), key=lambda item: str(item[0])):
        if 'http' not in values or 'grpc' not in values:
            continue
        http_run = values['http']
        grpc_run = values['grpc']
        http_p50 = http_run['client_latency_us']['p50']
        grpc_p50 = grpc_run['client_latency_us']['p50']
        http_throughput = http_run['throughput_ops_s']
        grpc_throughput = grpc_run['throughput_ops_s']
        comparisons.append({
            'operation': key[0],
            'strategy': key[1],
            'transport': key[2],
            'iterations': key[3],
            'concurrency': key[4],
            'http_p50_us': http_p50,
            'grpc_p50_us': grpc_p50,
            'grpc_latency_reduction_percent': (
                ((http_p50 - grpc_p50) / float(http_p50)) * 100
                if http_p50 else 0),
            'http_throughput_ops_s': http_throughput,
            'grpc_throughput_ops_s': grpc_throughput,
            'grpc_throughput_change_percent': (
                ((grpc_throughput - http_throughput) /
                 float(http_throughput)) * 100
                if http_throughput else 0),
        })
        comparison = comparisons[-1]
        if 'protocol_and_wire_us' in http_run:
            http_overhead = http_run['protocol_and_wire_us']['p50']
            grpc_overhead = grpc_run['protocol_and_wire_us']['p50']
            comparison.update({
                'http_protocol_and_wire_p50_us': http_overhead,
                'grpc_protocol_and_wire_p50_us': grpc_overhead,
                'grpc_protocol_overhead_reduction_percent': (
                    ((http_overhead - grpc_overhead) /
                     float(http_overhead)) * 100
                    if http_overhead else 0),
                'http_server_p50_us': http_run[
                    'server_total_us']['p50'],
                'grpc_server_p50_us': grpc_run[
                    'server_total_us']['p50'],
            })
    return comparisons


def run_suite(grpc_target, http_target, protocols, operations,
              strategies, transport, warmup, iterations,
              concurrency, timeout):
    discovery = GrpcClient(grpc_target, timeout)
    try:
        current = validate_pi(discovery.get_permutation()['pi'])
        runtime = discovery.get_runtime()
        backend = {
            'name': runtime.state.backend_capabilities.backend,
            'readback': runtime.state.backend_capabilities.readback,
            'native_batch': runtime.state.backend_capabilities.native_batch,
            'dataplane_atomic': (
                runtime.state.backend_capabilities.dataplane_atomic),
            'transports': list(
                runtime.state.backend_capabilities.transports),
        }
    finally:
        discovery.close()
    alternate = alternate_pi(current)

    runs = []
    for protocol in protocols:
        target = grpc_target if protocol == 'grpc' else http_target
        group = ClientGroup(protocol, target, timeout, concurrency)
        try:
            if 'read' in operations:
                runs.append(benchmark_read(
                    group, protocol, warmup, iterations, concurrency))
            for operation in ('noop', 'write'):
                if operation not in operations:
                    continue
                for strategy in strategies:
                    runs.append(benchmark_apply(
                        group, protocol, operation, current, alternate,
                        strategy, transport, warmup, iterations,
                        concurrency))
        finally:
            group.close()
    return {
        'benchmark': {
            'protocol_order': list(protocols),
            'operations': list(operations),
            'strategies': list(strategies),
            'transport': transport,
            'warmup': warmup,
            'iterations': iterations,
            'concurrency': concurrency,
            'timeout_seconds': timeout,
        },
        'client_runtime': {
            'python': platform.python_version(),
            'grpcio': getattr(grpc, '__version__', 'unknown'),
            'http': 'stdlib-http.client-persistent-http/1.1',
        },
        'grpc_target': grpc_target,
        'http_target': http_target,
        'port_count': len(current),
        'initial_pi': current,
        'alternate_pi': alternate,
        'backend': backend,
        'runs': runs,
        'comparisons': compare_protocols(runs),
    }


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description='Compare OCS HTTP and gRPC performance')
    parser.add_argument('--grpc-target', default='127.0.0.1:9339')
    parser.add_argument('--http-target', default='127.0.0.1:5000')
    parser.add_argument(
        '--protocol', choices=('grpc', 'http', 'both'), default='both')
    parser.add_argument(
        '--protocol-order', choices=('grpc-first', 'http-first'),
        default='grpc-first')
    parser.add_argument(
        '--operation', choices=('read', 'noop', 'write', 'all'),
        default='all')
    parser.add_argument(
        '--strategy', choices=('full', 'delta', 'both'), default='both')
    parser.add_argument(
        '--transport', choices=('sequential', 'native-batch'),
        default='sequential')
    parser.add_argument('--warmup', type=int, default=10)
    parser.add_argument('--iterations', type=int, default=200)
    parser.add_argument('--concurrency', type=int, default=1)
    parser.add_argument('--timeout', type=float, default=10.0)
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    if args.warmup < 0 or args.iterations < 1 or args.concurrency < 1:
        raise SystemExit(
            'warmup must be >= 0; iterations and concurrency must be >= 1')
    if args.protocol == 'both':
        protocols = (
            ('grpc', 'http') if args.protocol_order == 'grpc-first'
            else ('http', 'grpc'))
    else:
        protocols = (args.protocol,)
    operations = (
        ('read', 'noop', 'write') if args.operation == 'all'
        else (args.operation,))
    strategies = (
        ('FULL', 'DELTA') if args.strategy == 'both'
        else (args.strategy.upper(),))
    transport = args.transport.replace('-', '_').upper()
    result = run_suite(
        args.grpc_target, args.http_target, protocols, operations,
        strategies, transport, args.warmup, args.iterations,
        args.concurrency, args.timeout)
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == '__main__':
    main()
