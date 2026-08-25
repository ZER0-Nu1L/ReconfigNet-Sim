import http.client
import json
import os
import sys
import threading
import unittest


PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_DIR not in sys.path:
    sys.path.insert(0, PROJECT_DIR)


from api.northbound import create_rest_server, request_delay_us
from config.p4app_config import load_config, validate_config, validate_mapping
from p4util import ocs_map_p4runtime as mapping_module


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


class FakeP4InfoHelper(object):
    def buildTableEntry(self, **entry):
        return entry


class FakeRuntimeConnection(object):
    def __init__(self):
        self.entries = []
        self.fail_on_write = None

    def WriteTableEntry(self, entry):
        ports = entry_ports(entry)
        if self.fail_on_write == ports:
            self.fail_on_write = None
            raise RuntimeError('injected P4Runtime failure')
        self.entries.append(entry)

    def DeleteTableEntry(self, entry):
        self.entries.remove(entry)


class FakeRuntimeSwitch(object):
    def __init__(self):
        self.p4info_helper = FakeP4InfoHelper()
        self.sw_conn = FakeRuntimeConnection()

    def insertTableEntry(self, entry):
        raise AssertionError('swallowing wrapper must not be used')

    def removeTableEntry(self, entry):
        raise AssertionError('swallowing wrapper must not be used')


class OCSControlTests(unittest.TestCase):
    def setUp(self):
        config_path = os.path.join(PROJECT_DIR, 'config', 'p4app.json')
        self.config = load_config(config_path)
        self.num_hosts = self.config['num_hosts']
        self.initial_mapping = list(self.config['initial_mapping'])

    def initialized_state(self):
        switch = FakeSwitch()
        current = list(self.initial_mapping)
        state = {'status': 1, 'mode': 'ocs', 'revision': 0}
        mapping_module.init_ocs_mapping(
            switch, current, state, self.num_hosts)
        return switch, current, state

    def test_config_and_mapping_validation(self):
        self.assertEqual(self.num_hosts, 8)
        self.assertTrue(validate_mapping(self.initial_mapping, 8))
        with self.assertRaises(ValueError):
            validate_mapping([2, 3, 1, 4, 6, 5, 8, 7], 8)
        with self.assertRaises(ValueError):
            validate_mapping([1, 2, 4, 3, 6, 5, 8, 7], 8)
        with self.assertRaises(ValueError):
            validate_config({
                'mode': 'l3',
                'num_hosts': 10,
                'initial_mapping': list(range(1, 11)),
            })

    def test_mapping_update_and_idempotency(self):
        switch, current, state = self.initialized_state()
        target = [4, 3, 2, 1, 8, 7, 6, 5]
        success, result, timing = mapping_module.update_ocs_mapping(
            switch, target, current, state, self.num_hosts)
        self.assertTrue(success)
        self.assertEqual(result, 'updated')
        self.assertEqual(timing['active_entries'], 8)
        self.assertEqual(current, target)

        success, result, timing = mapping_module.update_ocs_mapping(
            switch, list(current), current, state, self.num_hosts)
        self.assertTrue(success)
        self.assertEqual(result, 'unchanged')
        self.assertEqual(timing['programming_total_us'], 0)

    def test_failed_update_restores_previous_mapping(self):
        switch, current, state = self.initialized_state()
        original_entries = list(switch.entries)
        switch.fail_on_insert = (1, 4)
        success, detail, timing = mapping_module.update_ocs_mapping(
            switch, [4, 3, 2, 1, 8, 7, 6, 5],
            current, state, self.num_hosts)
        self.assertFalse(success)
        self.assertIsNone(timing)
        self.assertIn('restored', detail)
        self.assertEqual(state['status'], 1)
        self.assertEqual(current, self.initial_mapping)
        self.assertEqual(switch.entries, original_entries)

    def test_failed_update_and_rollback_enters_error_state(self):
        switch, current, state = self.initialized_state()
        switch.fail_on_inserts = [(1, 4), (1, 2)]
        success, detail, timing = mapping_module.update_ocs_mapping(
            switch, [4, 3, 2, 1, 8, 7, 6, 5],
            current, state, self.num_hosts)
        self.assertFalse(success)
        self.assertIsNone(timing)
        self.assertIn('rollback failed', detail)
        self.assertEqual(state['status'], -2)
        self.assertEqual(current, self.initial_mapping)

    def test_p4runtime_adapter_propagates_write_failures(self):
        switch = FakeRuntimeSwitch()
        current = list(self.initial_mapping)
        state = {'status': 1, 'mode': 'ocs', 'revision': 0}
        mapping_module.init_ocs_mapping(
            switch, current, state, self.num_hosts)
        original_entries = list(switch.sw_conn.entries)
        switch.sw_conn.fail_on_write = (1, 4)

        success, detail, timing = mapping_module.update_ocs_mapping(
            switch, [4, 3, 2, 1, 8, 7, 6, 5],
            current, state, self.num_hosts)
        self.assertFalse(success)
        self.assertIsNone(timing)
        self.assertIn('restored', detail)
        self.assertEqual(switch.sw_conn.entries, original_entries)

    def test_debug_mode_installs_full_mesh_and_restores_mapping(self):
        switch, current, state = self.initialized_state()
        success, result, timing = mapping_module.update_ocs_mode(
            switch, 'debug', current, state, self.num_hosts)
        self.assertTrue(success)
        self.assertEqual(result, 'updated')
        self.assertEqual(state['mode'], 'debug')
        self.assertEqual(timing['active_entries'], 56)
        self.assertEqual(len(switch.entries), 56)

        success, detail, timing = mapping_module.update_ocs_mapping(
            switch, [4, 3, 2, 1, 8, 7, 6, 5],
            current, state, self.num_hosts)
        self.assertFalse(success)
        self.assertIn('debug mode', detail)
        self.assertIsNone(timing)

        success, result, timing = mapping_module.update_ocs_mode(
            switch, 'ocs', current, state, self.num_hosts)
        self.assertTrue(success)
        self.assertEqual(state['mode'], 'ocs')
        self.assertEqual(
            switch.entries,
            mapping_module.mapping_entries(current, self.num_hosts))

    def test_delay_validation(self):
        self.assertEqual(mapping_module.validate_delay_ms(1000), 1000)
        self.assertEqual(mapping_module.validate_delay_us(1000000), 1000000)
        for invalid in (-1, 1001, 1.5, True):
            with self.assertRaises(ValueError):
                mapping_module.validate_delay_ms(invalid)
        for invalid in (-1, 1000001, 1.5, True):
            with self.assertRaises(ValueError):
                mapping_module.validate_delay_us(invalid)
        self.assertEqual(request_delay_us({}), 0)
        self.assertEqual(request_delay_us({'delay_ms': 10}), 10000)
        self.assertEqual(request_delay_us({'delay_us': 250}), 250)
        with self.assertRaises(ValueError):
            request_delay_us({'delay_ms': 1, 'delay_us': 1000})

    def test_http_api_matches_tofino_semantics(self):
        switch, current, state = self.initialized_state()
        server = create_rest_server(
            current, state, switch, self.num_hosts, '127.0.0.1', 0)
        thread = threading.Thread(target=server.serve_forever)
        thread.daemon = True
        thread.start()

        def request(method, path, payload=None):
            body = None
            headers = {}
            if payload is not None:
                body = json.dumps(payload)
                headers['Content-Type'] = 'application/json'
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


if __name__ == '__main__':
    unittest.main()
