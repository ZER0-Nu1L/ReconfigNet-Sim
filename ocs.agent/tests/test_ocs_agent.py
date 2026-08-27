import os
import sys
import threading
import unittest


PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_DIR not in sys.path:
    sys.path.insert(0, PROJECT_DIR)


from agent.backend import P4appBackend, ocs_entry
from agent.core import OcsAgent
from agent.errors import (
    ApplyError,
    ConflictError,
    FailedPreconditionError,
    RevisionConflictError,
)
from agent.model import Connection, ConnectionSet, PortInventory
from config.p4app_config import load_config


def entry_pair(entry):
    fields = entry['match_fields']
    return (
        fields['standard_metadata.ingress_port'][0],
        fields['standard_metadata.egress_port'][0],
    )


class FakeSwitch(object):
    def __init__(self):
        self.entries = []
        self.fail_on_insert = None
        self.fail_on_delete = None

    def insertTableEntry(self, entry):
        pair = entry_pair(entry)
        if pair == self.fail_on_insert:
            self.fail_on_insert = None
            raise RuntimeError('injected insert failure {}'.format(pair))
        if entry in self.entries:
            raise RuntimeError('duplicate entry {}'.format(pair))
        self.entries.append(entry)

    def removeTableEntry(self, entry):
        pair = entry_pair(entry)
        if pair == self.fail_on_delete:
            self.fail_on_delete = None
            raise RuntimeError('injected delete failure {}'.format(pair))
        if entry not in self.entries:
            raise RuntimeError('missing entry {}'.format(pair))
        self.entries.remove(entry)


class BlockingFakeSwitch(FakeSwitch):
    def __init__(self):
        FakeSwitch.__init__(self)
        self.block_on_delete = None
        self.delete_entered = threading.Event()
        self.release_delete = threading.Event()

    def removeTableEntry(self, entry):
        pair = entry_pair(entry)
        if pair == self.block_on_delete:
            self.delete_entered.set()
            if not self.release_delete.wait(2):
                raise RuntimeError('timed out waiting to release delete')
            self.block_on_delete = None
        FakeSwitch.removeTableEntry(self, entry)


class OcsAgentTests(unittest.TestCase):
    def setUp(self):
        config_path = os.path.join(PROJECT_DIR, 'config', 'p4app.json')
        self.config = load_config(config_path)
        self.switch = FakeSwitch()
        self.agent = OcsAgent(
            self.config['model']['inventory'],
            self.config['initial_connections'],
            P4appBackend(self.switch),
            self.config['profile'], self.config['capability_profile'])
        self.lease = self.agent.acquire_control('unit-test')['lease_token']

    def tearDown(self):
        self.agent.close()

    def write(self, method, *args, **kwargs):
        kwargs.setdefault(
            'expected_revision', self.agent.snapshot()['revision'])
        kwargs.setdefault('lease_token', self.lease)
        return method(*args, **kwargs)

    def test_model_profile_is_authoritative(self):
        self.assertEqual(self.config['num_hosts'], 8)
        self.assertEqual(
            self.agent.get_permutation(),
            [2, 1, 4, 3, 6, 5, 8, 7])
        self.assertEqual(len(self.switch.entries), 8)

    def test_startup_replaces_stale_backend_entries(self):
        switch = FakeSwitch()
        switch.entries.append(ocs_entry(1, 3))
        agent = OcsAgent(
            self.config['model']['inventory'],
            self.config['initial_connections'],
            P4appBackend(switch),
            self.config['profile'], self.config['capability_profile'])
        self.assertEqual(
            agent.get_permutation(), [2, 1, 4, 3, 6, 5, 8, 7])
        self.assertNotIn((1, 3), set(entry_pair(item) for item in switch.entries))

    def test_sparse_delete_and_create(self):
        deleted = self.write(
            self.agent.delete_connection, 'conn-port-1-port-2')
        self.assertEqual(deleted['result'], 'updated')
        self.assertEqual(deleted['timing']['delete_entries'], 2)
        with self.assertRaises(FailedPreconditionError):
            self.agent.get_permutation()

        self.write(self.agent.delete_connection, 'conn-port-3-port-4')
        created = self.write(self.agent.replace_connection, Connection(
            'cross-1-3', 'port-1', 'port-3', True))
        self.assertEqual(created['result'], 'updated')
        tree = self.agent.openconfig_tree()
        connections = tree[
            'oc-optical-switch-connections:optical-switch-connections'][
                'port-connection']
        self.assertIn('cross-1-3', [item['connection-name'] for item in connections])

    def test_conflicting_port_reports_owner(self):
        with self.assertRaises(ConflictError) as context:
            self.agent.replace_connection(Connection(
                'conflict', 'port-1', 'port-3', True))
        self.assertEqual(context.exception.details['port_name'], 'port-1')
        self.assertEqual(
            context.exception.details['connection_name'],
            'conn-port-1-port-2')

    def test_full_and_delta_produce_same_target(self):
        target = [4, 3, 2, 1, 8, 7, 6, 5]
        full = self.write(
            self.agent.apply_permutation, target, strategy='FULL')
        self.assertEqual(full['timing']['delete_entries'], 8)
        self.assertEqual(full['timing']['insert_entries'], 8)
        self.assertIn('validation_us', full['timing'])
        self.assertIn('planning_us', full['timing'])

        original = [2, 1, 4, 3, 6, 5, 8, 7]
        delta = self.write(
            self.agent.apply_permutation, original, strategy='DELTA')
        self.assertEqual(delta['timing']['delete_entries'], 8)
        self.assertEqual(delta['timing']['insert_entries'], 8)
        self.assertEqual(self.agent.get_permutation(), original)

        partial_target = [2, 1, 6, 5, 4, 3, 8, 7]
        partial = self.write(
            self.agent.apply_permutation,
            partial_target, strategy='DELTA')
        self.assertEqual(partial['timing']['unchanged_entries'], 4)
        self.assertEqual(partial['timing']['delete_entries'], 4)
        self.assertEqual(partial['timing']['insert_entries'], 4)

    def test_revision_precondition(self):
        revision = self.agent.snapshot()['revision']
        self.agent.delete_connection(
            'conn-port-1-port-2', expected_revision=revision,
            lease_token=self.lease)
        with self.assertRaises(RevisionConflictError):
            self.agent.delete_connection(
                'conn-port-3-port-4', expected_revision=revision,
                lease_token=self.lease)

    def test_failed_update_restores_previous_entries(self):
        previous = set(entry_pair(entry) for entry in self.switch.entries)
        self.switch.fail_on_insert = (1, 4)
        with self.assertRaises(ApplyError) as context:
            self.write(
                self.agent.apply_permutation,
                [4, 3, 2, 1, 8, 7, 6, 5], strategy='FULL')
        self.assertTrue(context.exception.restored)
        self.assertEqual(
            set(entry_pair(entry) for entry in self.switch.entries), previous)
        self.assertEqual(self.agent.snapshot()['status'], 'ready')

    def test_debug_mode_blocks_connection_updates(self):
        response = self.write(self.agent.set_mode, 'debug')
        self.assertEqual(response['result'], 'updated')
        self.assertEqual(response['active_entries'], 56)
        with self.assertRaises(FailedPreconditionError):
            self.write(
                self.agent.delete_connection, 'conn-port-1-port-2')
        self.write(self.agent.set_mode, 'ocs')
        self.assertEqual(
            self.agent.get_permutation(),
            [2, 1, 4, 3, 6, 5, 8, 7])

    def test_concurrent_commits_are_serialized_and_revalidated(self):
        switch = BlockingFakeSwitch()
        agent = OcsAgent(
            self.config['model']['inventory'],
            self.config['initial_connections'],
            P4appBackend(switch),
            self.config['profile'], self.config['capability_profile'])
        switch.block_on_delete = (1, 2)
        results = []
        errors = []
        second_started = threading.Event()

        lease = agent.acquire_control('concurrency-test')['lease_token']
        revision = agent.snapshot()['revision']

        def delete(connection_name, started=None):
            try:
                if started is not None:
                    started.set()
                results.append(agent.delete_connection(
                    connection_name, expected_revision=revision,
                    lease_token=lease))
            except Exception as error:
                errors.append(error)

        first = threading.Thread(
            target=delete, args=('conn-port-1-port-2',))
        second = threading.Thread(
            target=delete,
            args=('conn-port-3-port-4', second_started))
        first.start()
        self.assertTrue(switch.delete_entered.wait(1))
        second.start()
        self.assertTrue(second_started.wait(1))
        threading.Event().wait(0.02)
        switch.release_delete.set()
        first.join(2)
        second.join(2)

        self.assertFalse(first.is_alive())
        self.assertFalse(second.is_alive())
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], RevisionConflictError)
        self.assertEqual(len(results), 1)
        self.assertEqual(agent.snapshot()['revision'], 1)
        self.assertIsNone(
            agent.get_connections().get('conn-port-1-port-2'))
        self.assertIsNotNone(
            agent.get_connections().get('conn-port-3-port-4'))
        agent.close()

    def test_lease_conflict_release_and_required_revision(self):
        with self.assertRaises(Exception) as context:
            self.agent.acquire_control('other')
        self.assertEqual(context.exception.code, 'RESOURCE_EXHAUSTED')
        with self.assertRaises(FailedPreconditionError):
            self.agent.delete_connection(
                'conn-port-1-port-2', lease_token=self.lease)
        state = self.agent.release_control(self.lease)
        self.assertFalse(state['active'])
        replacement = self.agent.acquire_control('other')
        self.assertGreater(replacement['lease_epoch'], 1)

    def test_commit_that_started_before_lease_expiry_completes(self):
        switch = BlockingFakeSwitch()
        agent = OcsAgent(
            self.config['model']['inventory'],
            self.config['initial_connections'], P4appBackend(switch),
            self.config['profile'], self.config['capability_profile'],
            lease_seconds=0.05, reconcile_interval_seconds=3600)
        lease = agent.acquire_control('short-lease')['lease_token']
        revision = agent.snapshot()['revision']
        switch.block_on_delete = (1, 2)
        result = []

        def update():
            result.append(agent.delete_connection(
                'conn-port-1-port-2', expected_revision=revision,
                lease_token=lease))

        thread = threading.Thread(target=update)
        thread.start()
        self.assertTrue(switch.delete_entered.wait(1))
        threading.Event().wait(0.07)
        switch.release_delete.set()
        thread.join(2)
        self.assertEqual(result[0]['result'], 'updated')
        self.assertFalse(agent.control_state()['active'])
        agent.close()


class ConnectionSetTests(unittest.TestCase):
    def test_permutation_round_trip(self):
        inventory = PortInventory([
            'port-1', 'port-2', 'port-3', 'port-4'])
        connections = ConnectionSet.from_permutation(
            inventory, [4, 3, 2, 1])
        self.assertEqual(connections.to_permutation(), [4, 3, 2, 1])


if __name__ == '__main__':
    unittest.main()
