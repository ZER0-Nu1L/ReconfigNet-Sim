#!/usr/bin/env python3
"""Measure sparse single-connection gNMI create/delete operations."""

from __future__ import print_function

import argparse
import json
import math
import platform
import sys
import time

import grpc

from ocs_agent.proto import gnmi_pb2
from ocs_agent.proto import gnmi_pb2_grpc
from ocs_agent.proto import ocs_operations_pb2
from ocs_agent.proto import ocs_operations_pb2_grpc


def monotonic_ns():
    try:
        return time.monotonic_ns()
    except AttributeError:
        return int(time.monotonic() * 1000000000)


def percentile(ordered, percent):
    index = int(math.ceil((percent / 100.0) * len(ordered))) - 1
    return ordered[max(0, min(index, len(ordered) - 1))]


def summary(values):
    ordered = sorted(values)
    return {
        'min': ordered[0],
        'mean': sum(ordered) / float(len(ordered)),
        'p50': percentile(ordered, 50),
        'p95': percentile(ordered, 95),
        'p99': percentile(ordered, 99),
        'max': ordered[-1],
    }


def connection_path(name):
    path = gnmi_pb2.Path()
    root = path.elem.add()
    root.name = (
        'oc-optical-switch-connections:optical-switch-connections')
    item = path.elem.add()
    item.name = 'port-connection'
    item.key['connection-name'] = name
    return path


def root_path():
    path = gnmi_pb2.Path()
    root = path.elem.add()
    root.name = (
        'oc-optical-switch-connections:optical-switch-connections')
    return path


def connection_json(name, near_port, far_port):
    return {
        'connection-name': name,
        'config': {
            'connection-name': name,
            'bidirectional': True,
            'near-port-name': near_port,
            'far-port-name': far_port,
        },
    }


def runtime_connection_json(connection):
    return connection_json(
        connection.connection_name,
        connection.near_port_name,
        connection.far_port_name)


class Session(object):
    def __init__(self, target, timeout):
        self.timeout = timeout
        self.channel = grpc.insecure_channel(target)
        self.operations = ocs_operations_pb2_grpc.OcsOperationsStub(
            self.channel)
        self.gnmi = gnmi_pb2_grpc.gNMIStub(self.channel)
        self.token = None
        self.revision = None

    def acquire(self):
        lease = self.operations.AcquireControl(
            ocs_operations_pb2.AcquireControlRequest(
                client_id='gnmi-connection-benchmark'),
            timeout=self.timeout)
        self.token = lease.lease_token
        self.revision = lease.revision

    def metadata(self):
        return (
            ('x-ocs-control-lease', self.token),
            ('x-ocs-expected-revision', str(self.revision)),
        )

    def set(self, request):
        started = monotonic_ns()
        response = self.gnmi.Set(
            request, metadata=self.metadata(), timeout=self.timeout)
        latency_us = int((monotonic_ns() - started) / 1000)
        operation = json.loads(response.message.message)
        self.revision = operation['revision']
        timing = operation['timing']
        return {
            'client_latency_us': latency_us,
            'result': operation['result'],
            'revision': operation['revision'],
            'timing': timing,
        }

    def replace_all(self, connections):
        request = gnmi_pb2.SetRequest()
        replace = request.replace.add()
        replace.path.CopyFrom(root_path())
        replace.val.json_ietf_val = json.dumps({
            'port-connection': connections,
        }, sort_keys=True).encode('utf-8')
        return self.set(request)

    def create(self, name, near_port, far_port):
        request = gnmi_pb2.SetRequest()
        replace = request.replace.add()
        replace.path.CopyFrom(connection_path(name))
        replace.val.json_ietf_val = json.dumps(
            connection_json(name, near_port, far_port),
            sort_keys=True).encode('utf-8')
        return self.set(request)

    def delete(self, name):
        request = gnmi_pb2.SetRequest()
        request.delete.add().CopyFrom(connection_path(name))
        return self.set(request)

    def refresh_revision(self):
        runtime = self.operations.GetRuntime(
            ocs_operations_pb2.Empty(), timeout=self.timeout)
        self.revision = runtime.state.revision
        return runtime

    def close(self):
        if self.token:
            try:
                self.operations.ReleaseControl(
                    ocs_operations_pb2.ReleaseControlRequest(
                        lease_token=self.token),
                    timeout=self.timeout)
            except grpc.RpcError:
                pass
        self.channel.close()


def build_run(operation, samples):
    timing_names = (
        'server_total_us',
        'queue_wait_us',
        'lease_revision_check_us',
        'validation_us',
        'planning_us',
        'delete_commit_us',
        'install_commit_us',
        'readback_us',
        'programming_total_us',
        'device_worker_rpc_us',
        'device_worker_total_us',
        'precondition_readback_us',
        'cache_precondition_us',
    )
    result = {
        'operation': operation,
        'iterations': len(samples),
        'results': {},
        'client_latency_us': summary([
            item['client_latency_us'] for item in samples]),
    }
    for item in samples:
        name = item['result']
        result['results'][name] = result['results'].get(name, 0) + 1
    for name in timing_names:
        result[name] = summary([
            item['timing'].get(name, 0) for item in samples])
    result['mean_delete_entries'] = sum(
        item['timing'].get('delete_entries', 0)
        for item in samples) / float(len(samples))
    result['mean_insert_entries'] = sum(
        item['timing'].get('insert_entries', 0)
        for item in samples) / float(len(samples))
    result['mean_device_write_requests'] = sum(
        item['timing'].get('device_write_requests', 0)
        for item in samples) / float(len(samples))
    return result


def run_benchmark(target, near_port, far_port, name,
                  warmup, iterations, timeout):
    session = Session(target, timeout)
    original = []
    started = monotonic_ns()
    try:
        session.acquire()
        runtime = session.operations.GetRuntime(
            ocs_operations_pb2.Empty(), timeout=timeout)
        original = [runtime_connection_json(connection)
                    for connection in runtime.state.connection_set.connections]
        session.revision = runtime.state.revision

        session.replace_all([])
        for _ in range(warmup):
            created = session.create(name, near_port, far_port)
            deleted = session.delete(name)
            if created['result'] != 'updated' or deleted['result'] != 'updated':
                raise RuntimeError('warmup was contaminated by a no-op')

        create_samples = []
        delete_samples = []
        for _ in range(iterations):
            create_samples.append(session.create(name, near_port, far_port))
            delete_samples.append(session.delete(name))

        for item in create_samples + delete_samples:
            if item['result'] != 'updated':
                raise RuntimeError(
                    'benchmark was contaminated by {}'.format(item['result']))
        for item in create_samples:
            if (item['timing'].get('insert_entries') != 2 or
                    item['timing'].get('delete_entries') != 0):
                raise RuntimeError(
                    'create must install exactly two directed entries')
        for item in delete_samples:
            if (item['timing'].get('delete_entries') != 2 or
                    item['timing'].get('insert_entries') != 0):
                raise RuntimeError(
                    'delete must remove exactly two directed entries')

        return {
            'schema': 'reconfig-net-ocs-gnmi-connection-benchmark/v1',
            'target': target,
            'client_runtime': {
                'language': 'python',
                'python': platform.python_version(),
                'grpc': getattr(grpc, '__version__', 'unknown'),
            },
            'benchmark': {
                'connection_name': name,
                'near_port_name': near_port,
                'far_port_name': far_port,
                'warmup': warmup,
                'iterations_per_operation': iterations,
                'concurrency': 1,
                'strategy': 'DELTA',
                'transport': 'SEQUENTIAL',
            },
            'elapsed_seconds': (
                monotonic_ns() - started) / 1000000000.0,
            'runs': [
                build_run('create', create_samples),
                build_run('delete', delete_samples),
            ],
        }
    finally:
        if session.token:
            try:
                session.refresh_revision()
                session.replace_all(original)
            finally:
                session.close()
        else:
            session.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--target', default='127.0.0.1:9339')
    parser.add_argument('--near-port', default='port-1')
    parser.add_argument('--far-port', default='port-2')
    parser.add_argument('--connection-name', default='benchmark-link')
    parser.add_argument('--warmup', type=int, default=10)
    parser.add_argument('--iterations', type=int, default=100)
    parser.add_argument('--timeout', type=float, default=10)
    parser.add_argument('--output', default='-')
    args = parser.parse_args()
    if args.warmup < 0 or args.iterations < 1:
        parser.error('warmup must be >=0 and iterations must be >=1')

    result = run_benchmark(
        args.target, args.near_port, args.far_port,
        args.connection_name, args.warmup, args.iterations, args.timeout)
    encoded = json.dumps(result, indent=2, sort_keys=True)
    if args.output == '-':
        print(encoded)
    else:
        with open(args.output, 'w') as file_obj:
            file_obj.write(encoded + '\n')


if __name__ == '__main__':
    try:
        main()
    except grpc.RpcError as error:
        print('gRPC benchmark failed: {}'.format(error), file=sys.stderr)
        sys.exit(1)
