#!/usr/bin/env python3
"""Measure client-to-Agent RTT sensitivity on an isolated Docker bridge.

The Agent container is temporarily attached to a dedicated bridge. Each
measurement uses a fresh privileged client container whose eth0 egress has
the requested netem delay. Agent-to-Worker/P4Runtime traffic is untouched.
"""

from __future__ import print_function

import argparse
import json
import os
import re
import subprocess


RTT_RE = re.compile(
    r'(?:rtt|round-trip) min/avg/max/(?:mdev|stddev) = '
    r'([0-9.]+)/([0-9.]+)/([0-9.]+)/([0-9.]+) ms')


def docker(*arguments):
    return subprocess.check_output(('docker',) + arguments)


def client_prefix(args):
    return (
        'run', '--rm', '--cap-add', 'NET_ADMIN',
        '--network', args.network, '--entrypoint', '/bin/sh',
        '-v', '{}:/work:ro'.format(args.project_dir), args.image,
    )


def netem(delay_ms):
    if delay_ms == 0:
        return ''
    return 'tc qdisc add dev eth0 root netem delay {}ms; '.format(delay_ms)


def ping_sample(args, delay_ms):
    command = netem(delay_ms) + 'ping -n -q -c 20 {}'.format(args.agent_alias)
    raw = docker(*(client_prefix(args) + ('-c', command))).decode('utf-8')
    match = RTT_RE.search(raw)
    if not match:
        raise RuntimeError('Unable to parse ping output: {}'.format(raw))
    values = [float(item) for item in match.groups()]
    return dict(zip(('min_ms', 'avg_ms', 'max_ms', 'mdev_ms'), values))


def benchmark_sample(args, delay_ms):
    command = netem(delay_ms) + (
        'exec python3 benchmarks/protocol_latency.py '
        '--grpc-target {alias}:9339 --http-target {alias}:5000 '
        '--protocol both --operation {operation} --strategy delta '
        '--transport native-batch --warmup {warmup} '
        '--iterations {iterations} --concurrency 1 --timeout {timeout}'
    ).format(
        alias=args.agent_alias, operation=args.operation,
        warmup=args.warmup, iterations=args.iterations,
        timeout=args.timeout)
    raw = docker(*(client_prefix(args) + ('-c', command)))
    return json.loads(raw.decode('utf-8'))


def run(args):
    created = False
    try:
        try:
            docker('network', 'inspect', args.network)
        except subprocess.CalledProcessError:
            docker('network', 'create', args.network)
            created = True
        try:
            docker('network', 'connect', '--alias', args.agent_alias,
                   args.network, args.agent_container)
        except subprocess.CalledProcessError:
            pass
        samples = []
        for delay_ms in args.added_rtt_ms:
            samples.append({
                'configured_added_rtt_ms': delay_ms,
                'ping': ping_sample(args, delay_ms),
                'suite': benchmark_sample(args, delay_ms),
            })
        return {
            'schema': 'reconfig-net-ocs-network-sweep/v1',
            'agent_container': args.agent_container,
            'network': args.network,
            'netem_scope': 'client-container eth0 egress only',
            'samples': samples,
        }
    finally:
        subprocess.call((
            'docker', 'network', 'disconnect', '-f', args.network,
            args.agent_container), stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL)
        if created:
            subprocess.call(('docker', 'network', 'rm', args.network),
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument('--agent-container', required=True)
    parser.add_argument('--agent-alias', default='ocs-agent')
    parser.add_argument('--network', default='ocs-benchmark-net')
    parser.add_argument(
        '--image', default='reconfig-net/ocs-benchmark-python:3.11')
    parser.add_argument('--project-dir', default=os.getcwd())
    parser.add_argument(
        '--added-rtt-ms', nargs='+', type=float,
        default=(0, 0.2, 0.5, 1, 2, 5, 10))
    parser.add_argument('--operation', choices=('read', 'noop', 'write'),
                        default='write')
    parser.add_argument('--warmup', type=int, default=10)
    parser.add_argument('--iterations', type=int, default=100)
    parser.add_argument('--timeout', type=float, default=15)
    parser.add_argument('--output', default='-')
    return parser.parse_args()


def main():
    args = parse_args()
    result = run(args)
    encoded = json.dumps(result, indent=2, sort_keys=True)
    if args.output == '-':
        print(encoded)
    else:
        with open(args.output, 'w') as file_obj:
            file_obj.write(encoded + '\n')


if __name__ == '__main__':
    main()
