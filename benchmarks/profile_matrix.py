#!/usr/bin/env python3
"""Collect and compare the two supported OCS deployment profiles.

Collection changes the active mapping and must run only on a test system.
Run ``collect`` once while each profile owns the same backend, then pass both
documents to ``report``.
"""

from __future__ import print_function

import argparse
import json
import subprocess


PYTHON_HTTP = 'python-monolith-http-direct'
GO_GRPC = 'go-split-grpc'
PROFILES = (PYTHON_HTTP, GO_GRPC)
PROFILE_PROTOCOL = {
    PYTHON_HTTP: 'http',
    GO_GRPC: 'grpc',
}


def _run_benchmark(args, concurrency):
    command = [
        args.benchmark,
        '--runtime', args.profile,
        '--grpc-target', args.grpc_target,
        '--http-target', args.http_target,
        '--protocol', PROFILE_PROTOCOL[args.profile],
        '--operation', 'write',
        '--strategy', 'both',
        '--transport', 'native-batch',
        '--warmup', str(args.warmup),
        '--iterations', str(args.iterations),
        '--concurrency', str(concurrency),
        '--timeout', '{}s'.format(args.timeout),
    ]
    output = subprocess.check_output(command)
    return json.loads(output.decode('utf-8'))


def collect(args):
    suites = [
        _run_benchmark(args, concurrency)
        for concurrency in (1, 4)
    ]
    document = {
        'schema': 'reconfig-net-ocs-frontier-matrix/v2',
        'deployment_profile': args.profile,
        'nbi_protocol': PROFILE_PROTOCOL[args.profile],
        'client_runtime': suites[0]['client_runtime'],
        'backend': suites[0]['backend'],
        'port_count': suites[0]['port_count'],
        'suites': suites,
    }
    _write_json(document, args.output)


def _write_json(document, path):
    encoded = json.dumps(document, indent=2, sort_keys=True)
    if path == '-':
        print(encoded)
        return
    with open(path, 'w') as file_obj:
        file_obj.write(encoded)
        file_obj.write('\n')


def _load_documents(paths):
    documents = {}
    for path in paths:
        with open(path, 'r') as file_obj:
            document = json.load(file_obj)
        profile = document.get('deployment_profile')
        if profile not in PROFILES:
            raise ValueError(
                '{} has unsupported deployment profile {}'.format(
                    path, profile))
        if profile in documents:
            raise ValueError('Duplicate result for {}'.format(profile))
        if document.get('schema') != 'reconfig-net-ocs-frontier-matrix/v2':
            raise ValueError('{} has an unsupported schema'.format(path))
        expected_protocol = PROFILE_PROTOCOL[profile]
        if document.get('nbi_protocol') != expected_protocol:
            raise ValueError(
                '{} must use {} for {}'.format(
                    path, expected_protocol, profile))
        documents[profile] = document
    return documents


def _runs(document):
    result = {}
    for suite in document['suites']:
        for run in suite['runs']:
            key = (
                run['protocol'], run['operation'], run.get('strategy'),
                run.get('transport'), run['concurrency'])
            result[key] = run
    return result


def _run(runs, protocol, strategy, concurrency):
    key = (protocol, 'write', strategy, 'NATIVE_BATCH', concurrency)
    if key not in runs:
        raise ValueError('Missing benchmark run {}'.format(key))
    return runs[key]


def _summary_p50(run, name):
    summary = run.get(name) or {}
    return summary.get('p50', 0)


def _percent_change(new, old):
    if not old:
        return 0.0
    return ((new - old) / float(old)) * 100.0


def _matrix(documents):
    rows = []
    for profile in PROFILES:
        if profile not in documents:
            continue
        protocol = PROFILE_PROTOCOL[profile]
        runs = _runs(documents[profile])
        delta_c1 = _run(runs, protocol, 'DELTA', 1)
        delta_c4 = _run(runs, protocol, 'DELTA', 4)
        full_c1 = _run(runs, protocol, 'FULL', 1)
        rows.append({
            'deployment_profile': profile,
            'agent_language': 'python' if profile == PYTHON_HTTP else 'go',
            'nbi_protocol': protocol,
            'worker_boundary': profile == GO_GRPC,
            'native_delta_c1_p50_us': delta_c1[
                'client_latency_us']['p50'],
            'native_delta_c1_p99_us': delta_c1[
                'client_latency_us']['p99'],
            'native_delta_c4_throughput_ops_s': delta_c4[
                'throughput_ops_s'],
            'native_full_c1_p50_us': full_c1[
                'client_latency_us']['p50'],
            'device_worker_rpc_c1_p50_us': _summary_p50(
                delta_c1, 'device_worker_rpc_us'),
            'device_worker_total_c1_p50_us': _summary_p50(
                delta_c1, 'device_worker_total_us'),
            'exclusive_delta_c1_mean_us': dict(
                (name, values['mean'])
                for name, values in delta_c1.get(
                    'exclusive_breakdown_us', {}).items()),
            'exclusive_delta_c1_p50_us': dict(
                (name, values['p50'])
                for name, values in delta_c1.get(
                    'exclusive_breakdown_us', {}).items()),
        })
    return rows


def _frontier_comparison(matrix):
    indexed = dict(
        (item['deployment_profile'], item) for item in matrix)
    if PYTHON_HTTP not in indexed or GO_GRPC not in indexed:
        return {
            'evaluated': False,
            'reason': 'both supported deployment profiles are required',
        }
    baseline = indexed[PYTHON_HTTP]
    split = indexed[GO_GRPC]
    delta_us = (
        split['native_delta_c1_p50_us'] -
        baseline['native_delta_c1_p50_us'])
    full_us = (
        split['native_full_c1_p50_us'] -
        baseline['native_full_c1_p50_us'])
    return {
        'evaluated': True,
        'latency_frontier': PYTHON_HTTP,
        'isolation_frontier': GO_GRPC,
        'metrics': {
            'split_minus_monolith_delta_c1_p50_us': delta_us,
            'split_minus_monolith_delta_c1_p50_percent': _percent_change(
                split['native_delta_c1_p50_us'],
                baseline['native_delta_c1_p50_us']),
            'split_minus_monolith_full_c1_p50_us': full_us,
            'split_minus_monolith_full_c1_p50_percent': _percent_change(
                split['native_full_c1_p50_us'],
                baseline['native_full_c1_p50_us']),
            'split_minus_monolith_delta_c1_p99_percent': _percent_change(
                split['native_delta_c1_p99_us'],
                baseline['native_delta_c1_p99_us']),
            'split_vs_monolith_delta_c4_throughput_percent': _percent_change(
                split['native_delta_c4_throughput_ops_s'],
                baseline['native_delta_c4_throughput_ops_s']),
        },
    }


def report(args):
    documents = _load_documents(args.input)
    matrix = _matrix(documents)
    result = {
        'schema': 'reconfig-net-ocs-frontier-report/v2',
        'inputs': sorted(documents),
        'matrix': matrix,
        'frontier_comparison': _frontier_comparison(matrix),
    }
    _write_json(result, args.output)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description='Collect or report the two OCS deployment frontiers')
    subparsers = parser.add_subparsers(dest='command')

    collect_parser = subparsers.add_parser('collect')
    collect_parser.add_argument('--profile', required=True, choices=PROFILES)
    collect_parser.add_argument(
        '--benchmark', default='/usr/local/bin/ocs-benchmark')
    collect_parser.add_argument('--grpc-target', default='127.0.0.1:9339')
    collect_parser.add_argument('--http-target', default='127.0.0.1:5000')
    collect_parser.add_argument('--warmup', type=int, default=10)
    collect_parser.add_argument('--iterations', type=int, default=100)
    collect_parser.add_argument('--timeout', type=float, default=10.0)
    collect_parser.add_argument('--output', default='-')
    collect_parser.set_defaults(run=collect)

    report_parser = subparsers.add_parser('report')
    report_parser.add_argument('--input', action='append', required=True)
    report_parser.add_argument('--output', default='-')
    report_parser.set_defaults(run=report)

    args = parser.parse_args(argv)
    if not hasattr(args, 'run'):
        parser.error('collect or report is required')
    if args.command == 'collect':
        if args.warmup < 0 or args.iterations < 1:
            parser.error('warmup must be >=0 and iterations must be >=1')
        if args.iterations > 104:
            parser.error(
                'iterations must be <=104 for uncontaminated 8-port c4 writes')
    return args


def main(argv=None):
    args = parse_args(argv)
    try:
        args.run(args)
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        raise SystemExit(str(error))


if __name__ == '__main__':
    main()
