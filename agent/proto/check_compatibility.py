#!/usr/bin/env python3
"""Exercise real RPCs and upgrade/downgrade against a persistent fake device.

No physical device or switch SDK is accessed. Works in Python 3.5 and modern
Python so the exact same probes can run on both sides of the wire.
"""

from __future__ import print_function

import argparse
import hashlib
import json
import os
import platform
import signal
import socket
import subprocess
import sys
import tempfile
import threading

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
SOURCE = os.environ.get('OCS_COMPAT_SOURCE_ROOT', ROOT)
sys.path[:0] = [os.path.join(SOURCE, 'agent', 'python'), SOURCE]

import grpc
import google.protobuf
from ocs_agent.backends.p4app import P4AppBackend, ocs_entry
from ocs_agent.device_worker import create_device_worker_server
from ocs_agent.proto import device_backend_pb2 as pb
from ocs_agent.proto import device_backend_pb2_grpc as rpc
from ocs_agent.proto import gnmi_pb2, gnmi_pb2_grpc
from ocs_agent.proto import ocs_operations_pb2 as op
from ocs_agent.proto import ocs_operations_pb2_grpc as op_rpc

INITIAL = [[1, 2], [2, 1], [3, 4], [4, 3],
           [5, 6], [6, 5], [7, 8], [8, 7]]


def snapshot(args):
    """Compare schemas, boundary wire values and YAML across actual runtimes."""
    from google.protobuf import descriptor_pb2, json_format
    from ocs_agent.proto import gnmi_ext_pb2
    import yaml

    def normalize(value):
        if isinstance(value, dict):
            return {key: normalize(item) for key, item in value.items()
                    if key not in ('json_name', 'source_code_info')}
        if isinstance(value, list):
            return [normalize(item) for item in value]
        return value

    schemas = {}
    for module in (pb, op, gnmi_pb2, gnmi_ext_pb2):
        descriptor = descriptor_pb2.FileDescriptorProto()
        descriptor.ParseFromString(module.DESCRIPTOR.serialized_pb)
        schemas[descriptor.name] = normalize(json_format.MessageToDict(
            descriptor, preserving_proto_field_name=True))
    values = [op.ApplyBatchRequest(has_expected_revision=True,
              expected_revision=0, permutation=op.Permutation(pi=[2, 1])),
              pb.ReadEntriesResponse(generation=(1 << 64) - 1,
                                     cache_status='READY'),
              gnmi_pb2.TypedValue(json_ietf_val=b'{"mode":"l3"}')]
    wire = []
    for value in values:
        # Unknown field 127, uint64=1 must survive decoding and re-encoding.
        encoded = value.SerializeToString() + b'\xf8\x07\x01'
        decoded = type(value).FromString(encoded)
        assert decoded.SerializeToString() == encoded
        wire.append(encoded.hex())
    configs = {}
    directory = os.path.join(ROOT, 'agent', 'configs')
    for parent, _, names in os.walk(directory):
        for name in names:
            if name.endswith(('.yaml', '.yml')):
                path = os.path.join(parent, name)
                with open(path, encoding='utf-8') as stream:
                    configs[os.path.relpath(path, directory)] = yaml.safe_load(stream)
    try:
        yaml.safe_load('!!python/object/apply:os.system ["false"]')
        raise AssertionError('unsafe YAML constructor accepted')
    except yaml.constructor.ConstructorError:
        pass
    payload = {'schemas': schemas, 'wire': wire, 'configs': configs}
    print(json.dumps(payload, sort_keys=True))


def pairs(messages):
    return sorted((item.ingress_port, item.egress_port) for item in messages)


def messages(values):
    return [pb.PortPair(ingress_port=a, egress_port=b) for a, b in values]


class PersistentSwitch(object):
    def __init__(self, state, failure):
        self.state = state
        self.failure = failure
        values = INITIAL
        if os.path.exists(state):
            with open(state) as stream:
                values = json.load(stream)
        self.entries = [ocs_entry(a, b) for a, b in values]
        self.save()

    def save(self):
        values = []
        for entry in self.entries:
            fields = entry['match_fields']
            values.append([fields['standard_metadata.ingress_port'][0],
                           fields['standard_metadata.egress_port'][0]])
        with open(self.state + '.tmp', 'w') as stream:
            json.dump(sorted(values), stream)
        os.replace(self.state + '.tmp', self.state)

    def insertTableEntry(self, entry):
        if os.path.exists(self.failure):
            with open(self.failure) as stream:
                remaining = int(stream.read())
            if remaining > 1:
                with open(self.failure, 'w') as stream:
                    stream.write(str(remaining - 1))
            else:
                os.unlink(self.failure)
            raise RuntimeError('injected insert failure')
        if entry in self.entries:
            raise RuntimeError('duplicate entry')
        self.entries.append(entry)
        self.save()

    def removeTableEntry(self, entry):
        self.entries.remove(entry)
        self.save()


def worker(args):
    backend = P4AppBackend(PersistentSwitch(args.state, args.failure))
    server = create_device_worker_server(backend, args.target)
    server.start()
    stopped = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stopped.set())
    signal.signal(signal.SIGINT, lambda *_: stopped.set())
    try:
        stopped.wait()
    finally:
        server.stop(5).wait()


def probe(args):
    with grpc.insecure_channel(args.target) as channel:
        grpc.channel_ready_future(channel).result(timeout=15)
        stub = rpc.DeviceBackendStub(channel)
        initial = pairs(stub.ReadEntries(pb.Empty(), timeout=3).entries)
        state = stub.ReadEntries(pb.Empty(), timeout=3)
        alternate = [(1, 3), (3, 1)]
        if initial == alternate:
            alternate = [(1, 2), (2, 1)]

        def apply(target, generation, expected):
            return stub.ApplyTransition(pb.ApplyTransitionRequest(
                expected_entries=messages(expected),
                target_entries=messages(target), expected_generation=generation,
                strategy='FULL', transport='SEQUENTIAL'), timeout=3)

        assert stub.Capabilities(pb.Empty(), timeout=3).readback
        changed = apply(alternate, state.generation, initial)
        assert changed.success and pairs(changed.observed_entries) == alternate
        stale = apply(initial, state.generation, alternate)
        assert not stale.success and stale.error_code == 'FAILED_PRECONDITION'
        for count, restored in [(1, True), (2, False)]:
            with open(args.failure, 'w') as stream:
                stream.write(str(count))
            failed = apply(initial, changed.generation, alternate)
            assert not failed.success and failed.restored == restored
            assert failed.error_code == ('ABORTED' if restored else 'INTERNAL')
            if not restored:
                assert failed.cache_status == 'UNKNOWN'
            recovered = stub.Recover(pb.RecoverRequest(
                desired_entries=messages(alternate), strategy='FULL',
                transport='SEQUENTIAL'), timeout=3)
            assert recovered.success and recovered.cache_status == 'READY'
            changed = recovered
        restored = stub.Recover(pb.RecoverRequest(
            desired_entries=messages(initial), strategy='FULL',
            transport='SEQUENTIAL'), timeout=3)
        assert restored.success and pairs(restored.observed_entries) == initial
    print(json.dumps({'python': platform.python_version(),
                      'grpcio': grpc.__version__,
                      'protobuf': google.protobuf.__version__,
                      'checks': ['read', 'write', 'stale-generation',
                                 'transaction-rollback', 'rollback-failure',
                                 'explicit-recovery', 'restore-initial']}))


def go_probe(args):
    target = args.target
    with grpc.insecure_channel(target) as channel:
        grpc.channel_ready_future(channel).result(timeout=20)
        stub = op_rpc.OcsOperationsStub(channel)
        capabilities = gnmi_pb2_grpc.gNMIStub(channel).Capabilities(
            gnmi_pb2.CapabilityRequest(), timeout=3)
        assert capabilities.gNMI_version == '0.10.0'
        assert not stub.GetControlState(op.Empty(), timeout=3).active
        lease = stub.AcquireControl(op.AcquireControlRequest(
            client_id='compatibility'), timeout=3)
        metadata = (('x-ocs-control-lease', lease.lease_token),)
        before = stub.GetRuntime(op.Empty(), timeout=3)
        if args.require_mismatch:
            assert before.state.status == op.RUNTIME_STATUS_ERROR
        if before.state.status == op.RUNTIME_STATUS_ERROR:
            reply = stub.RecoverDeviceState(op.RecoverDeviceStateRequest(
                mode=op.RECOVERY_MODE_REAPPLY_DESIRED,
                has_expected_revision=True,
                expected_revision=before.state.revision),
                metadata=metadata, timeout=3)
            assert reply.state.status == op.RUNTIME_STATUS_READY
        initial = stub.GetPermutation(op.Empty(), timeout=3)
        current = list(initial.permutation.pi)
        # Use a complete bijective involution supported by the 8-port model.
        alternate = [4, 3, 2, 1, 8, 7, 6, 5]
        request = op.ApplyBatchRequest(
            permutation=op.Permutation(pi=alternate),
            strategy=op.EXECUTION_STRATEGY_DELTA,
            transport=op.TRANSPORT_SEQUENTIAL,
            has_expected_revision=True,
            expected_revision=initial.state.revision)
        reply = stub.ApplyBatch(request, metadata=metadata, timeout=3)
        assert reply.state.status == op.RUNTIME_STATUS_READY
        try:
            stub.ApplyBatch(request, metadata=metadata, timeout=3)
            raise AssertionError('stale revision was accepted')
        except grpc.RpcError as error:
            assert error.code() == grpc.StatusCode.ABORTED
        request.permutation.pi[:] = current
        request.expected_revision = reply.state.revision
        stub.ApplyBatch(request, metadata=metadata, timeout=3)
        restored = stub.GetPermutation(op.Empty(), timeout=3)
        assert list(restored.permutation.pi) == current
        stub.ReleaseControl(op.ReleaseControlRequest(
            lease_token=lease.lease_token), timeout=3)
        assert not stub.GetControlState(op.Empty(), timeout=3).active
    print(json.dumps({'python': platform.python_version(),
                      'checks': ['gnmi', 'require-match', 'explicit-recovery',
                                 'lease', 'revision', 'write', 'restore']}))


def stop(process):
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
            raise RuntimeError('Test process did not stop gracefully')


def matrix(args):
    records = []
    with tempfile.TemporaryDirectory(prefix='oc-') as temporary:
        state = os.path.join(temporary, 'device.json')
        failure = os.path.join(temporary, 'failure')
        target = 'unix://' + os.path.join(temporary, 'worker.sock')
        legacy = ['docker', 'run', '--rm', '--network', 'host',
                  '--user', '{}:{}'.format(os.getuid(), os.getgid()),
                  '-v', ROOT + ':' + ROOT + ':ro',
                  '-v', temporary + ':' + temporary,
                  '-e', 'PYTHONDONTWRITEBYTECODE=1',
                  '-e', 'OCS_COMPAT_SOURCE_ROOT=' + args.legacy_source,
                  '--entrypoint', 'python3', args.legacy_image]
        if args.legacy_source != ROOT:
            legacy[2:2] = ['-v', args.legacy_source + ':' + args.legacy_source + ':ro']
        snapshots = []
        for command in ([sys.executable], legacy):
            output = subprocess.check_output(command + [__file__, 'snapshot'])
            snapshots.append(json.loads(output.decode()))
        assert snapshots[0] == snapshots[1], 'schema, wire or YAML mismatch'
        schema_hash = hashlib.sha256(json.dumps(
            snapshots[0], sort_keys=True).encode()).hexdigest()
        commands = [('legacy', legacy), ('modern', [sys.executable]),
                    ('downgrade', legacy)]
        for flavor, command in commands:
            log_path = os.path.join(temporary, flavor + '.log')
            with open(log_path, 'w') as log:
                process = subprocess.Popen(command + [__file__, 'worker',
                    '--target', target, '--state', state, '--failure', failure],
                    stdout=log, stderr=log)
                try:
                    with grpc.insecure_channel(target) as channel:
                        grpc.channel_ready_future(channel).result(timeout=20)
                        stub = rpc.DeviceBackendStub(channel)
                        before = stub.ReadEntries(pb.Empty(), timeout=3)
                        assert before.generation == 1
                        assert pairs(before.entries) == [tuple(item) for item in INITIAL]
                    for client_name, client in [('modern', [sys.executable]),
                                                ('legacy', legacy)]:
                        output = subprocess.check_output(client + [__file__,
                            'probe', '--target', target, '--failure', failure])
                        records.append({'worker': flavor, 'client': client_name,
                                        'result': json.loads(output.decode())})
                    for binary in args.go_agent:
                        # Deliberately mismatch the model. REQUIRE_MATCH must
                        # leave this state untouched until explicit recovery.
                        with grpc.insecure_channel(target) as channel:
                            stub = rpc.DeviceBackendStub(channel)
                            reply = stub.Recover(pb.RecoverRequest(
                                desired_entries=messages([(1, 2), (2, 1)]),
                                strategy='FULL', transport='SEQUENTIAL'), timeout=3)
                            assert reply.success
                        with socket.socket() as port:
                            port.bind(('127.0.0.1', 0))
                            number = port.getsockname()[1]
                        config_dir = os.path.join(ROOT, 'agent', 'configs', 'p4app')
                        with open(os.path.join(config_dir, 'go-split-grpc.json')) as stream:
                            config = json.load(stream)
                        for key in ('model_file', 'capability_profile_file'):
                            config[key] = os.path.join(config_dir, config[key])
                        config['worker']['target'] = target
                        config['grpc_api'] = {'host': '127.0.0.1', 'port': number}
                        config['startup_policy'] = 'REQUIRE_MATCH'
                        config_path = os.path.join(temporary, 'agent.json')
                        with open(config_path, 'w') as stream:
                            json.dump(config, stream)
                        go = subprocess.Popen([binary, '--config', config_path],
                                              stdout=log, stderr=log)
                        try:
                            go_target = '127.0.0.1:' + str(number)
                            with grpc.insecure_channel(go_target) as channel:
                                grpc.channel_ready_future(channel).result(timeout=20)
                            with open(state) as stream:
                                assert json.load(stream) == [[1, 2], [2, 1]]
                            for client_name, client in [('modern', [sys.executable]),
                                                        ('legacy', legacy)]:
                                # The first client recovers startup mismatch;
                                # the second acquires a fresh lease after it.
                                options = ['--require-mismatch'] if client_name == 'modern' else []
                                output = subprocess.check_output(client + [__file__,
                                    'go-probe', '--target', go_target] + options)
                                records.append({'worker': flavor, 'go_agent': binary,
                                                'client': client_name,
                                                'result': json.loads(output.decode())})
                        finally:
                            stop(go)
                except BaseException:
                    log.flush()
                    with open(log_path) as stream:
                        print(stream.read(), file=sys.stderr)
                    raise
                finally:
                    stop(process)
            with open(state) as stream:
                assert json.load(stream) == INITIAL, 'mapping not restored: ' + flavor
        with open(state) as stream:
            restored = json.load(stream)
        print(json.dumps({'schema': 'ocs-rpc-compatibility/v1',
                          'scope': 'persistent fake device; no hardware',
                          'passed': True, 'records': records,
                          'schema_wire_yaml_sha256': schema_hash,
                          'initial_pairs': INITIAL,
                          'final_pairs': restored}, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    if not __debug__:
        parser.error('Do not disable compatibility assertions with -O')
    subparsers = parser.add_subparsers(dest='command')
    for name in ('worker', 'probe', 'go-probe'):
        child = subparsers.add_parser(name)
        child.add_argument('--target', required=True)
        if name != 'go-probe':
            child.add_argument('--failure', required=True)
        else:
            child.add_argument('--require-mismatch', action='store_true')
        if name == 'worker':
            child.add_argument('--state', required=True)
        child.set_defaults(run={'worker': worker, 'probe': probe,
                                'go-probe': go_probe}[name])
    subparsers.add_parser('snapshot').set_defaults(run=snapshot)
    child = subparsers.add_parser('matrix')
    child.add_argument('--legacy-image', default='reconfig-net/ocs-p4app-agent-test:rc2')
    child.add_argument('--go-agent', action='append', default=[])
    child.add_argument('--legacy-source', default=ROOT)
    child.set_defaults(run=matrix)
    args = parser.parse_args()
    if not hasattr(args, 'run'):
        parser.error('a subcommand is required')
    args.run(args)


if __name__ == '__main__':
    main()
