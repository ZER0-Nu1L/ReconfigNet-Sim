import os
import sys
import unittest


PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_DIR not in sys.path:
    sys.path.insert(0, PROJECT_DIR)


from agent import bfrt_backend
from agent.backend import BackendTransitionError
from agent.core import OcsAgent
from agent.errors import FailedPreconditionError
from agent.model import ConnectionSet, PortInventory


PORT_MAP = {
    '1': 132,
    '2': 140,
    '3': 148,
    '4': 156,
    '5': 180,
    '6': 188,
}


class FakeTuple(object):
    def __init__(self, name, value):
        self.name = name
        self.value = value


class FakeKey(object):
    def __init__(self, tuples):
        self.tuples = tuples

    def to_dict(self):
        return dict(
            (item.name, {'value': item.value}) for item in self.tuples)


class FakeTable(object):
    def __init__(self, entries=None):
        self.entries = set(entries or ())
        self.calls = []
        self.read_calls = []
        self.fail_next_add = False

    @staticmethod
    def _pair(key):
        values = dict((item.name, item.value) for item in key.tuples)
        return (
            values[bfrt_backend.DEFAULT_INGRESS_FIELD],
            values[bfrt_backend.DEFAULT_EGRESS_FIELD])

    def make_key(self, tuples):
        return FakeKey(tuples)

    def make_data(self, values, action_name=None):
        return {'values': values, 'action_name': action_name}

    def entry_get(self, target, keys, flags):
        self.read_calls.append(bool(flags.get('from_hw')))
        for ingress, egress in sorted(self.entries):
            yield None, FakeKey([
                FakeTuple(bfrt_backend.DEFAULT_INGRESS_FIELD, ingress),
                FakeTuple(bfrt_backend.DEFAULT_EGRESS_FIELD, egress),
            ])

    def entry_del(self, target, keys):
        pairs = [self._pair(key) for key in keys]
        self.calls.append(('delete', pairs))
        for pair in pairs:
            if pair not in self.entries:
                raise RuntimeError('missing {}'.format(pair))
            self.entries.remove(pair)

    def entry_add(self, target, keys, values):
        pairs = [self._pair(key) for key in keys]
        self.calls.append(('insert', pairs))
        if self.fail_next_add:
            self.fail_next_add = False
            raise RuntimeError('injected BFRT add failure')
        for pair in pairs:
            if pair in self.entries:
                raise RuntimeError('duplicate {}'.format(pair))
            self.entries.add(pair)


class FakeInfo(object):
    def __init__(self, table):
        self.table = table

    def table_get(self, name):
        if name != bfrt_backend.DEFAULT_TABLE:
            raise KeyError(name)
        return self.table


class FakeInterface(object):
    def __init__(self, table):
        self.table = table
        self.bound = None
        self.closed = False

    def bind_pipeline_config(self, name):
        self.bound = name

    def bfrt_info_get(self, name):
        return FakeInfo(self.table)

    def _tear_down_stream(self):
        self.closed = True


class FakeGC(object):
    def __init__(self, table):
        self.table = table
        self.interface = None

    class Notifications(object):
        def __init__(self, **kwargs):
            self.options = kwargs

    class Target(object):
        def __init__(self, **kwargs):
            self.options = kwargs

    KeyTuple = FakeTuple

    def ClientInterface(self, *args, **kwargs):
        self.interface = FakeInterface(self.table)
        return self.interface


class BfrtBackendTests(unittest.TestCase):
    def setUp(self):
        self.original_loader = bfrt_backend._load_bfrt_client

    def tearDown(self):
        bfrt_backend._load_bfrt_client = self.original_loader

    def backend(self, entries=None, consistency='CACHED_SYNC'):
        table = FakeTable(entries)
        gc = FakeGC(table)
        bfrt_backend._load_bfrt_client = lambda unused=None: gc
        backend = bfrt_backend.BfrtBackend({
            'type': 'bfrt',
            'logical_to_device_port': PORT_MAP,
        }, consistency_mode=consistency)
        return backend, table, gc

    def test_translates_logical_ports_and_hardware_audit(self):
        backend, table, unused = self.backend([(132, 188), (188, 132)])
        self.assertEqual(backend.read_pairs(), set(((1, 6), (6, 1))))
        pairs, elapsed_us = backend.audit_hardware()
        self.assertEqual(pairs, set(((1, 6), (6, 1))))
        self.assertGreaterEqual(elapsed_us, 0)
        self.assertEqual(table.read_calls, [False, True])
        self.assertEqual(
            backend.device_state()['readback_source'], 'BFRT_HARDWARE')

    def test_sequential_and_native_batch_programming(self):
        previous = set(((1, 6), (6, 1)))
        target = set(((2, 3), (3, 2)))
        backend, table, unused = self.backend(
            [(132, 188), (188, 132)])
        timing = backend.apply(
            previous, target, strategy='DELTA', transport='SEQUENTIAL')
        self.assertEqual(timing['device_write_requests'], 4)
        self.assertEqual(table.entries, set(((140, 148), (148, 140))))

        timing = backend.apply(
            target, previous, strategy='DELTA', transport='NATIVE_BATCH')
        self.assertEqual(timing['device_write_requests'], 2)
        self.assertEqual(len(table.calls[-2][1]), 2)
        self.assertEqual(len(table.calls[-1][1]), 2)
        self.assertEqual(table.entries, set(((132, 188), (188, 132))))

    def test_cached_ack_omits_post_write_readback(self):
        backend, table, unused = self.backend(
            [(132, 188), (188, 132)], 'CACHED_ACK')
        previous = backend.read_pairs()
        reads_before = len(table.read_calls)
        timing = backend.apply(
            previous, set(((2, 3), (3, 2))),
            strategy='DELTA', transport='NATIVE_BATCH')
        self.assertEqual(len(table.read_calls), reads_before)
        self.assertEqual(timing['write_verification'], 'ACK')
        self.assertEqual(timing['readback_source'], '')
        self.assertGreater(
            backend.device_state()['last_write_ack_unix_ns'], 0)

    def test_failure_rolls_back_with_software_readback(self):
        backend, table, unused = self.backend(
            [(132, 188), (188, 132)], 'CACHED_ACK')
        previous = set(((1, 6), (6, 1)))
        table.fail_next_add = True
        with self.assertRaises(BackendTransitionError) as context:
            backend.apply(
                previous, set(((2, 3), (3, 2))),
                strategy='DELTA', transport='NATIVE_BATCH')
        self.assertTrue(context.exception.restored)
        self.assertEqual(table.entries, set(((132, 188), (188, 132))))
        self.assertIn(False, table.read_calls)

    def test_require_match_stays_blocked_until_explicit_recovery(self):
        backend, table, unused = self.backend(
            [(140, 148), (148, 140)], 'CACHED_ACK')
        inventory = PortInventory([
            'port-1', 'port-2', 'port-3', 'port-4', 'port-5', 'port-6'])
        desired = ConnectionSet.from_permutation(
            inventory, [6, 3, 2, 5, 4, 1])
        agent = OcsAgent(
            inventory, desired, backend,
            consistency_mode='CACHED_ACK',
            reconcile_interval_seconds=3600,
            startup_policy='REQUIRE_MATCH')
        try:
            snapshot = agent.snapshot()
            self.assertEqual(snapshot['status'], 'error')
            self.assertTrue(snapshot['device_state'][
                'startup_recovery_required'])
            lease = agent.acquire_control('startup-test')['lease_token']

            table.entries = set((
                (132, 188), (188, 132),
                (140, 148), (148, 140),
                (156, 180), (180, 156)))
            agent.reconcile_device_state()
            self.assertEqual(agent.snapshot()['status'], 'error')
            with self.assertRaises(FailedPreconditionError):
                agent.apply_permutation(
                    [6, 3, 2, 5, 4, 1], strategy='DELTA',
                    expected_revision=0, lease_token=lease)

            result = agent.recover_device_state(
                expected_revision=0, lease_token=lease,
                transport='NATIVE_BATCH')
            self.assertEqual(result['result'], 'recovered')
            self.assertEqual(result['timing']['write_verification'],
                             'SOFTWARE_READBACK')
            self.assertFalse(agent.snapshot()['device_state'][
                'startup_recovery_required'])
            self.assertEqual(agent.snapshot()['status'], 'ready')
        finally:
            agent.close()


if __name__ == '__main__':
    unittest.main()
