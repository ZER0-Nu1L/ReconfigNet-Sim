#!/usr/bin/env python3
"""Summarize one or more OCS dual-target fast-switch probe groups."""

from __future__ import print_function

import argparse
import glob
import json
import math
import os


def _percentile(values, percentile):
    if not values:
        return 0
    ordered = sorted(values)
    index = max(0, int(math.ceil(percentile * len(ordered))) - 1)
    return ordered[index]


def _metric(samples, name):
    values = [item[name] for item in samples if item.get(name) is not None]
    return {
        'min': min(values) if values else 0,
        'p50': _percentile(values, 0.50),
        'p95': _percentile(values, 0.95),
        'p99': _percentile(values, 0.99),
        'max': max(values) if values else 0,
    }


def summarize_group(label, paths):
    documents = []
    samples = []
    for path in sorted(paths):
        with open(path, 'r') as file_obj:
            document = json.load(file_obj)
        if document.get('schema') != 'reconfig-net-ocs-fast-switch/v1':
            raise ValueError('{} is not a fast-switch result'.format(path))
        documents.append(os.path.abspath(path))
        samples.extend(document.get('samples', []))
    successful = [item for item in samples if item.get('success')]
    return {
        'label': label,
        'documents': documents,
        'sample_count': len(samples),
        'success_count': len(successful),
        'success_rate': (
            float(len(successful)) / len(samples) if samples else 0),
        'request_to_ack_us': _metric(successful, 'request_to_ack_us'),
        'request_to_first_new_us': _metric(
            successful, 'request_to_first_new_us'),
        'last_old_to_first_new_blackout_us': _metric(
            successful, 'last_old_to_first_new_blackout_us'),
    }


def parse_group(value):
    if '=' not in value:
        raise argparse.ArgumentTypeError('group must be LABEL=GLOB')
    label, pattern = value.split('=', 1)
    paths = glob.glob(pattern)
    if not label or not paths:
        raise argparse.ArgumentTypeError(
            'group label and at least one matching file are required')
    return label, paths


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        '--group', action='append', required=True, type=parse_group,
        help='result group expressed as LABEL=GLOB')
    parser.add_argument('--output', default='-')
    args = parser.parse_args()
    result = {
        'schema': 'reconfig-net-ocs-fast-switch-summary/v1',
        'groups': [summarize_group(label, paths)
                   for label, paths in args.group],
    }
    encoded = json.dumps(result, indent=2, sort_keys=True) + '\n'
    if args.output == '-':
        print(encoded, end='')
    else:
        with open(args.output, 'w') as file_obj:
            file_obj.write(encoded)


if __name__ == '__main__':
    main()
