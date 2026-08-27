import os
import sys
import unittest


PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_DIR not in sys.path:
    sys.path.insert(0, PROJECT_DIR)


from benchmarks.benchmark_matrix import (
    GO_GRPC,
    PYTHON_HTTP,
    _frontier_comparison,
    _matrix,
)


def run(protocol, strategy, concurrency, p50, p99, throughput,
        with_worker):
    result = {
        'protocol': protocol,
        'operation': 'write',
        'strategy': strategy,
        'transport': 'NATIVE_BATCH',
        'concurrency': concurrency,
        'throughput_ops_s': throughput,
        'client_latency_us': {'p50': p50, 'p99': p99},
        'exclusive_breakdown_us': {
            'client_non_server': {'mean': 10, 'p50': 9},
            'validation': {'mean': 2, 'p50': 1},
        },
    }
    if with_worker:
        result['device_worker_rpc_us'] = {'p50': 80}
        result['device_worker_total_us'] = {'p50': 60}
    return result


def document(profile, scale):
    protocol = 'http' if profile == PYTHON_HTTP else 'grpc'
    with_worker = profile == GO_GRPC
    runs = []
    for strategy in ('FULL', 'DELTA'):
        strategy_scale = scale * (1.2 if strategy == 'FULL' else 1.0)
        runs.append(run(
            protocol, strategy, 1,
            int(1000 * strategy_scale),
            int(1400 * strategy_scale),
            100.0 / strategy_scale,
            with_worker))
        runs.append(run(
            protocol, strategy, 4,
            int(4000 * strategy_scale),
            int(5000 * strategy_scale),
            400.0 / strategy_scale,
            with_worker))
    return {
        'deployment_profile': profile,
        'suites': [{'runs': runs}],
    }


class BenchmarkMatrixTests(unittest.TestCase):
    def test_matrix_contains_only_supported_frontiers(self):
        documents = {
            PYTHON_HTTP: document(PYTHON_HTTP, 1.0),
            GO_GRPC: document(GO_GRPC, 1.2),
        }
        matrix = _matrix(documents)
        self.assertEqual(len(matrix), 2)
        self.assertEqual(
            [item['deployment_profile'] for item in matrix],
            [PYTHON_HTTP, GO_GRPC])
        self.assertEqual(matrix[0]['device_worker_rpc_c1_p50_us'], 0)
        self.assertEqual(matrix[1]['device_worker_rpc_c1_p50_us'], 80)

    def test_frontier_comparison_reports_absolute_latency_cost(self):
        matrix = _matrix({
            PYTHON_HTTP: document(PYTHON_HTTP, 1.0),
            GO_GRPC: document(GO_GRPC, 1.2),
        })
        comparison = _frontier_comparison(matrix)
        self.assertTrue(comparison['evaluated'])
        self.assertEqual(
            comparison['latency_frontier'], PYTHON_HTTP)
        self.assertEqual(
            comparison['isolation_frontier'], GO_GRPC)
        self.assertEqual(
            comparison['metrics'][
                'split_minus_monolith_delta_c1_p50_us'],
            200)


if __name__ == '__main__':
    unittest.main()
