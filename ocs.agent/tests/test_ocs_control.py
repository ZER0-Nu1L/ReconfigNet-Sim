import http.client
import json
import os
import sys
import threading
import unittest
from unittest import mock


PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_DIR not in sys.path:
    sys.path.insert(0, PROJECT_DIR)


from agent.backend import BackendTransitionError, P4appBackend
from agent.core import OcsAgent
from agent.execution import DedicatedThreadBackend
from api.http_server import create_rest_server, request_delay_us
from config.p4app_config import load_config


def entry_ports(entry):
    fields = entry['match_fields']
    return (
        fields['standard_metadata.ingress_port'][0],
        fields['standard_metadata.egress_port'][0],
    )


class FakeSwitch(object):
    def __init__(self):
        self.entries = []
        self.fail_on_insert = None
        self.fail_on_inserts = []

    def insertTableEntry(self, entry):
        ports = entry_ports(entry)
        if self.fail_on_insert == ports:
            self.fail_on_insert = None
            raise RuntimeError('injected table failure')
        if ports in self.fail_on_inserts:
            self.fail_on_inserts.remove(ports)
            raise RuntimeError('injected table failure {}'.format(ports))
        if entry in self.entries:
            raise RuntimeError('duplicate table entry {}'.format(ports))
        self.entries.append(entry)

    def removeTableEntry(self, entry):
        if entry not in self.entries:
            raise RuntimeError('missing table entry {}'.format(entry_ports(entry)))
        self.entries.remove(entry)


class OCSControlTests(unittest.TestCase):
    def setUp(self):
        config_path = os.path.join(PROJECT_DIR, 'config', 'p4app.json')
        self.config = load_config(config_path)
        self.num_hosts = self.config['num_hosts']
        self.initial_mapping = list(self.config['initial_mapping'])

    def test_p4app_config_loads_canonical_model(self):
        self.assertEqual(self.num_hosts, 8)
        self.assertEqual(
            self.config['deployment_profile'],
            'python-monolith-http-direct')
        self.assertEqual(
            self.config['initial_connections'].to_permutation(),
            self.initial_mapping)

    def test_consistency_environment_override(self):
        config_path = os.path.join(PROJECT_DIR, 'config', 'p4app.json')
        with mock.patch.dict(
                os.environ, {
                    'OCS_CONSISTENCY_MODE': 'STRICT_DEVICE',
                }):
            config = load_config(config_path)
        self.assertEqual(
            config['deployment_profile'],
            'python-monolith-http-direct')
        self.assertEqual(
            config['device']['consistency_mode'], 'STRICT_DEVICE')

    def test_dedicated_thread_backend_preserves_operations(self):
        switch = FakeSwitch()
        backend = DedicatedThreadBackend(P4appBackend(switch))
        try:
            timing = backend.apply(
                set(), set(((1, 2), (2, 1))),
                strategy='FULL', transport='SEQUENTIAL')
            self.assertEqual(backend.read_pairs(), set(((1, 2), (2, 1))))
            self.assertEqual(timing['insert_entries'], 2)
            self.assertEqual(timing['device_write_requests'], 2)
        finally:
            backend.close()

    def test_p4app_backend_restores_previous_entries(self):
        switch = FakeSwitch()
        backend = P4appBackend(switch)
        previous = self.config['initial_connections'].directed_pairs()
        target = set(((1, 4), (4, 1), (2, 3), (3, 2),
                      (5, 8), (8, 5), (6, 7), (7, 6)))
        backend.apply(set(), previous, strategy='FULL')
        switch.fail_on_insert = (1, 4)
        with self.assertRaises(BackendTransitionError) as raised:
            backend.apply(previous, target, strategy='FULL')
        self.assertTrue(raised.exception.restored)
        self.assertEqual(backend.read_pairs(), previous)

    def test_p4app_backend_reports_rollback_failure(self):
        switch = FakeSwitch()
        backend = P4appBackend(switch)
        previous = self.config['initial_connections'].directed_pairs()
        target = set(((1, 4), (4, 1), (2, 3), (3, 2),
                      (5, 8), (8, 5), (6, 7), (7, 6)))
        backend.apply(set(), previous, strategy='FULL')
        switch.fail_on_inserts = [(1, 4), (1, 2)]
        with self.assertRaises(BackendTransitionError) as raised:
            backend.apply(previous, target, strategy='FULL')
        self.assertFalse(raised.exception.restored)
        self.assertIsNotNone(raised.exception.rollback_error)

    def test_http_delay_validation(self):
        self.assertEqual(request_delay_us({}), 0)
        self.assertEqual(request_delay_us({'delay_ms': 10}), 10000)
        self.assertEqual(request_delay_us({'delay_us': 250}), 250)
        with self.assertRaises(ValueError):
            request_delay_us({'delay_ms': 1, 'delay_us': 1000})

    def test_http_api_matches_tofino_semantics(self):
        switch = FakeSwitch()
        agent = OcsAgent(
            self.config['model']['inventory'],
            self.config['initial_connections'],
            P4appBackend(switch),
            self.config['profile'], self.config['capability_profile'])
        server = create_rest_server(agent, '127.0.0.1', 0)
        lease = agent.acquire_control('http-test')['lease_token']
        thread = threading.Thread(target=server.serve_forever)
        thread.daemon = True
        thread.start()

        def request(method, path, payload=None):
            body = None
            headers = {}
            if payload is not None:
                body = json.dumps(payload)
                headers['Content-Type'] = 'application/json'
            if method == 'POST':
                headers['X-OCS-Control-Lease'] = lease
                headers['X-OCS-Expected-Revision'] = str(
                    agent.snapshot()['revision'])
            connection = http.client.HTTPConnection(
                '127.0.0.1', server.server_port, timeout=2)
            try:
                connection.request(method, path, body=body, headers=headers)
                response = connection.getresponse()
                response_body = json.loads(response.read().decode('utf-8'))
                return response.status, response_body
            finally:
                connection.close()

        try:
            status, payload = request('GET', '/ocs_mapping')
            self.assertEqual(status, 200)
            self.assertEqual(payload['status'], 'ready')
            self.assertEqual(payload['mode'], 'ocs')

            status, payload = request(
                'POST', '/ocs_mode', {'mode': 'debug', 'delay_us': 0})
            self.assertEqual(status, 200)
            self.assertEqual(payload['status'], 'success')
            self.assertEqual(payload['state'], 'ready')
            self.assertEqual(payload['revision'], 1)
            self.assertEqual(payload['timing']['active_entries'], 56)

            status, payload = request(
                'POST', '/ocs_mapping',
                {'new_pi': [4, 3, 2, 1, 8, 7, 6, 5]})
            self.assertEqual(status, 409)
            self.assertIn('debug mode', payload['error'])

            status, payload = request(
                'POST', '/ocs_mapping',
                {'new_pi': [1, 2, 4, 3, 6, 5, 8, 7]})
            self.assertEqual(status, 400)

            status, payload = request(
                'POST', '/ocs_mapping',
                {'new_pi': self.initial_mapping, 'transport': 1})
            self.assertEqual(status, 400)

            status, payload = request(
                'POST', '/ocs_mode', {'mode': 'ocs'})
            self.assertEqual(status, 200)
            self.assertEqual(payload['revision'], 2)

            target = [4, 3, 2, 1, 8, 7, 6, 5]
            status, payload = request(
                'POST', '/ocs_mapping', {'new_pi': target})
            self.assertEqual(status, 200)
            self.assertEqual(payload['result'], 'updated')
            self.assertEqual(payload['revision'], 3)

            status, payload = request(
                'POST', '/ocs_mapping', {'new_pi': target})
            self.assertEqual(status, 200)
            self.assertEqual(payload['result'], 'unchanged')
            self.assertEqual(payload['revision'], 3)

            status, payload = request(
                'POST', '/ocs_mode',
                {'mode': 'ocs', 'delay_ms': 1, 'delay_us': 1000})
            self.assertEqual(status, 400)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)
            agent.close()


if __name__ == '__main__':
    unittest.main()
