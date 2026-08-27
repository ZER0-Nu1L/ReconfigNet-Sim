import os
import shutil
import sys
import tempfile
import unittest


PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_DIR not in sys.path:
    sys.path.insert(0, PROJECT_DIR)


try:
    import grpc
    from ocs_agent.backends.p4app import P4AppBackend
    from ocs_agent.device_worker import (
        cleanup_device_worker_target,
        create_device_worker_server,
    )
    from ocs_agent.proto import device_backend_pb2, device_backend_pb2_grpc
    from tests.test_ocs_agent import FakeSwitch, entry_pair
    GRPC_AVAILABLE = True
except ImportError:
    GRPC_AVAILABLE = False


def port_pairs(values):
    return [
        device_backend_pb2.PortPair(
            ingress_port=ingress, egress_port=egress)
        for ingress, egress in values
    ]


@unittest.skipUnless(GRPC_AVAILABLE, 'grpc/protobuf dependencies unavailable')
class DeviceWorkerTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix='ocs-worker-test-')
        self.target = 'unix://{}'.format(
            os.path.join(self.temp_dir, 'worker.sock'))
        self.switch = FakeSwitch()
        self.server = create_device_worker_server(
            P4AppBackend(self.switch), self.target,
            consistency_mode='CACHED_SYNC')
        self.server.start()
        self.channel = grpc.insecure_channel(self.target)
        self.stub = device_backend_pb2_grpc.DeviceBackendStub(
            self.channel)

    def tearDown(self):
        self.channel.close()
        self.server.stop(0).wait()
        cleanup_device_worker_target(self.target)
        shutil.rmtree(self.temp_dir)

    def apply(self, expected, target, generation=1):
        return self.stub.ApplyTransition(
            device_backend_pb2.ApplyTransitionRequest(
                expected_entries=port_pairs(expected),
                target_entries=port_pairs(target),
                strategy='FULL',
                transport='SEQUENTIAL',
                expected_generation=generation),
            timeout=2)

    def test_capabilities_and_initial_cache_are_exposed(self):
        capabilities = self.stub.Capabilities(
            device_backend_pb2.Empty(), timeout=2)
        self.assertEqual(capabilities.backend, 'p4app')
        self.assertTrue(capabilities.readback)
        self.assertIn('SEQUENTIAL', capabilities.transports)

        state = self.stub.ReadEntries(
            device_backend_pb2.Empty(), timeout=2)
        self.assertEqual(state.generation, 1)
        self.assertEqual(state.cache_status, 'READY')
        self.assertEqual(list(state.entries), [])

    def test_apply_updates_device_and_worker_cache(self):
        target = ((1, 2), (2, 1))
        response = self.apply((), target)

        self.assertTrue(response.success)
        self.assertTrue(response.restored)
        self.assertEqual(response.generation, 2)
        self.assertEqual(response.cache_status, 'READY')
        self.assertEqual(response.timing.insert_entries, 2)
        self.assertEqual(response.timing.device_write_requests, 2)
        self.assertGreaterEqual(response.timing.device_worker_total_us, 0)
        self.assertEqual(
            set(entry_pair(item) for item in self.switch.entries),
            set(target))

        state = self.stub.ReadEntries(
            device_backend_pb2.Empty(), timeout=2)
        self.assertEqual(
            set((item.ingress_port, item.egress_port)
                for item in state.entries),
            set(target))

    def test_generation_mismatch_is_rejected_without_writes(self):
        initial = ((1, 2), (2, 1))
        self.assertTrue(self.apply((), initial).success)
        before = list(self.switch.entries)

        response = self.apply(
            initial, ((1, 3), (3, 1)), generation=1)

        self.assertFalse(response.success)
        self.assertEqual(response.error_code, 'FAILED_PRECONDITION')
        self.assertEqual(self.switch.entries, before)
        self.assertEqual(response.generation, 2)

    def test_drift_requires_reconcile_then_explicit_recovery(self):
        desired = ((1, 2), (2, 1))
        self.assertTrue(self.apply((), desired).success)
        self.switch.entries.pop()

        reconciled = self.stub.Reconcile(
            device_backend_pb2.ReconcileRequest(
                desired_entries=port_pairs(desired)),
            timeout=2)
        self.assertEqual(reconciled.cache_status, 'DRIFTED')
        self.assertEqual(reconciled.drift_count, 1)

        blocked = self.apply(
            desired, ((1, 3), (3, 1)), generation=2)
        self.assertFalse(blocked.success)
        self.assertEqual(blocked.error_code, 'FAILED_PRECONDITION')

        recovered = self.stub.Recover(
            device_backend_pb2.RecoverRequest(
                desired_entries=port_pairs(desired),
                strategy='FULL',
                transport='SEQUENTIAL'),
            timeout=2)
        self.assertTrue(recovered.success)
        self.assertEqual(recovered.cache_status, 'READY')
        self.assertEqual(recovered.generation, 3)
        self.assertEqual(
            set(entry_pair(item) for item in self.switch.entries),
            set(desired))


if __name__ == '__main__':
    unittest.main()
