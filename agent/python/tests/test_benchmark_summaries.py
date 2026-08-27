from __future__ import print_function

import json
import os
import shutil
import tempfile
import unittest

from benchmarks.report_execution_ab import summarize
from benchmarks.report_fast_switch import summarize_group


def _write(path, document):
    with open(path, 'w') as file_obj:
        json.dump(document, file_obj)


def _run(protocol, strategy, latency, programming, queue):
    metric = {'p50': latency, 'p99': latency + 10}
    return {
        'protocol': protocol,
        'operation': 'write',
        'strategy': strategy,
        'client_latency_us': metric,
        'server_total_us': {'p50': latency - 10},
        'programming_total_us': {'p50': programming},
        'delete_commit_us': {'p50': 10},
        'install_commit_us': {'p50': 20},
        'readback_us': {'p50': 30},
        'exclusive_breakdown_us': {
            'queue_wait': {'p50': queue},
            'core_residual': {'p50': 40},
            'client_non_server': {'p50': 50},
        },
        'throughput_ops_s': 100.0,
    }


class BenchmarkSummaryTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.directory)

    def test_execution_summary_supports_prefix_and_nested_queue(self):
        for execution, latency in (('direct', 100), ('dedicated', 80)):
            _write(os.path.join(
                self.directory,
                'ack-{}-r1-grpc.json'.format(execution)), {
                    'runtime_label': execution,
                    'runs': [_run('grpc', 'DELTA', latency, 60, 7)],
                })
        result = summarize(self.directory, 'ack')
        self.assertEqual(len(result['rows']), 2)
        self.assertEqual(result['rows'][0]['southbound_queue_p50_us'], 7)
        self.assertEqual(
            result['comparisons'][0]['dedicated_minus_direct_p50_us'],
            -20)

    def test_fast_switch_summary_uses_successful_samples(self):
        path = os.path.join(self.directory, 'probe.json')
        _write(path, {
            'schema': 'reconfig-net-ocs-fast-switch/v1',
            'samples': [
                {
                    'success': True,
                    'request_to_ack_us': 10,
                    'request_to_first_new_us': 8,
                    'last_old_to_first_new_blackout_us': 5,
                },
                {
                    'success': True,
                    'request_to_ack_us': 20,
                    'request_to_first_new_us': 18,
                    'last_old_to_first_new_blackout_us': 7,
                },
                {'success': False},
            ],
        })
        result = summarize_group('candidate', [path])
        self.assertEqual(result['sample_count'], 3)
        self.assertEqual(result['success_count'], 2)
        self.assertEqual(result['request_to_ack_us']['p50'], 10)
        self.assertEqual(
            result['last_old_to_first_new_blackout_us']['p99'], 7)


if __name__ == '__main__':
    unittest.main()
