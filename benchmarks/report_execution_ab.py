#!/usr/bin/env python3
"""Summarize repeated DIRECT/dedicated OCS benchmark artifacts."""

from __future__ import print_function

import argparse
import glob
import json
import os
import statistics


def _load(path):
    with open(path, 'r') as file_obj:
        return json.load(file_obj)


def _run(document, protocol, strategy):
    for item in document['runs']:
        if (item['protocol'] == protocol and
                item.get('operation') == 'write' and
                item.get('strategy') == strategy):
            return item
    raise ValueError(
        '{} lacks {} {}'.format(
            document.get('runtime_label'), protocol, strategy))


def _runs(documents, protocol, strategy):
    selected = []
    for document in documents:
        try:
            selected.append(_run(document, protocol, strategy))
        except ValueError:
            continue
    return selected


def _median(values):
    return statistics.median(values) if values else 0


def _metric(runs, name, field='p50'):
    return _median([
        item.get(name, {}).get(field, 0) for item in runs
    ])


def _exclusive_metric(runs, name, field='p50'):
    return _median([
        item.get('exclusive_breakdown_us', {}).get(name, {}).get(field, 0)
        for item in runs
    ])


def _documents(directory, prefix, execution, protocol, concurrency):
    marker = '-c4-' if concurrency == 4 else '-r'
    stem = '{}-{}'.format(prefix, execution) if prefix else execution
    pattern = '{}{}*{}*.json'.format(stem, marker, protocol)
    return [_load(path) for path in sorted(glob.glob(
        os.path.join(directory, pattern)))]


def summarize(directory, prefix=''):
    rows = []
    indexed = {}
    for execution in ('direct', 'dedicated'):
        for protocol in ('http', 'grpc'):
            c1_documents = _documents(
                directory, prefix, execution, protocol, 1)
            c4_documents = _documents(
                directory, prefix, execution, protocol, 4)
            if not c1_documents:
                continue
            for strategy in ('FULL', 'DELTA'):
                c1 = _runs(c1_documents, protocol, strategy)
                if not c1:
                    continue
                c4 = []
                if strategy == 'DELTA':
                    c4 = _runs(c4_documents, protocol, strategy)
                row = {
                    'execution': execution.upper(),
                    'protocol': protocol,
                    'strategy': strategy,
                    'rounds': len(c1),
                    'client_p50_us': _metric(c1, 'client_latency_us'),
                    'client_p99_us': _metric(
                        c1, 'client_latency_us', 'p99'),
                    'server_p50_us': _metric(c1, 'server_total_us'),
                    'programming_p50_us': _metric(
                        c1, 'programming_total_us'),
                    'delete_p50_us': _metric(c1, 'delete_commit_us'),
                    'install_p50_us': _metric(c1, 'install_commit_us'),
                    'readback_p50_us': _metric(c1, 'readback_us'),
                    'southbound_queue_p50_us': _exclusive_metric(
                        c1, 'queue_wait'),
                    'core_residual_p50_us': _exclusive_metric(
                        c1, 'core_residual'),
                    'client_non_server_p50_us': _exclusive_metric(
                        c1, 'client_non_server'),
                    'c1_throughput_ops_s': _median([
                        item['throughput_ops_s'] for item in c1]),
                    'c4_throughput_ops_s': _median([
                        item['throughput_ops_s'] for item in c4]),
                }
                rows.append(row)
                indexed[(execution, protocol, strategy)] = row
    comparisons = []
    for protocol in ('http', 'grpc'):
        for strategy in ('FULL', 'DELTA'):
            direct = indexed.get(('direct', protocol, strategy))
            dedicated = indexed.get(('dedicated', protocol, strategy))
            if direct is None or dedicated is None:
                continue
            comparisons.append({
                'protocol': protocol,
                'strategy': strategy,
                'dedicated_minus_direct_p50_us': (
                    dedicated['client_p50_us'] - direct['client_p50_us']),
                'dedicated_minus_direct_p99_us': (
                    dedicated['client_p99_us'] - direct['client_p99_us']),
                'dedicated_minus_direct_programming_p50_us': (
                    dedicated['programming_p50_us'] -
                    direct['programming_p50_us']),
                'dedicated_minus_direct_server_p50_us': (
                    dedicated['server_p50_us'] - direct['server_p50_us']),
            })
    return {
        'schema': 'reconfig-net-ocs-execution-ab-summary/v1',
        'directory': os.path.abspath(directory),
        'prefix': prefix,
        'rows': rows,
        'comparisons': comparisons,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--directory', required=True)
    parser.add_argument('--prefix', default='')
    parser.add_argument('--output', default='-')
    args = parser.parse_args()
    encoded = json.dumps(
        summarize(args.directory, args.prefix), indent=2, sort_keys=True)
    if args.output == '-':
        print(encoded)
    else:
        with open(args.output, 'w') as file_obj:
            file_obj.write(encoded + '\n')


if __name__ == '__main__':
    main()
