import importlib
import os
import sys
import types
import unittest


NET_CTRL = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if NET_CTRL not in sys.path:
    sys.path.insert(0, NET_CTRL)


class FakeBfrt(object):
    def __init__(self):
        self.complete_count = 0

    def complete_operations(self):
        self.complete_count += 1


fake_bfrt = FakeBfrt()
fake_bfrtcli = types.ModuleType('bfrtcli')
fake_bfrtcli.bfrt = fake_bfrt
sys.modules['bfrtcli'] = fake_bfrtcli

from config.custom_connect import load_config, validate_mapping
mapping_module = importlib.import_module('p4util.ocs_map_p4runtime')
northbound_module = importlib.import_module('api.northbound')


class FakeTable(object):
    def __init__(self):
        self.entries = []
        self.fail_on_add = None

    def clear(self):
        self.entries = []

    def add_with_permit_ocs(self, ingress_port, ucast_egress_port):
        entry = (ingress_port, ucast_egress_port)
        if self.fail_on_add == entry:
            self.fail_on_add = None
            raise RuntimeError('injected table failure')
        self.entries.append(entry)


class FakePipe(object):
    def __init__(self):
        self.SwitchIngress = types.SimpleNamespace(ocs_mapping=FakeTable())


class OCSControlTests(unittest.TestCase):
    def setUp(self):
        config_path = os.path.join(NET_CTRL, 'config', 'project_conf.json')
        self.config = load_config(config_path)
        self.endpoints = self.config['endpoints']

    def test_example_profile_is_site_neutral(self):
        self.assertEqual(self.config['fabric'], 'example')
        self.assertEqual(self.config['rest_api']['host'], '127.0.0.1')
        self.assertEqual(self.config['num_hosts'], 6)
        self.assertEqual(self.config['initial_mapping'], [6, 3, 2, 5, 4, 1])
        self.assertTrue(all(
            endpoint['ipv4'].startswith('192.0.2.')
            for endpoint in self.config['endpoints']))

    def test_mapping_requires_symmetric_pairs(self):
        with self.assertRaises(ValueError):
            validate_mapping([2, 3, 1, 5, 6, 4], 6)
        with self.assertRaises(ValueError):
            validate_mapping([1, 2, 3, 4, 5, 6], 6)
        self.assertTrue(validate_mapping([2, 1, 6, 5, 4, 3], 6))

    def test_mapping_update_and_idempotency(self):
        pipe = FakePipe()
        current = list(self.config['initial_mapping'])
        state = {'status': 1, 'mode': 'ocs', 'revision': 0}
        mapping_module.init_ocs_mapping(pipe, current, state, self.endpoints)
        success, result, timing = mapping_module.update_ocs_mapping(
            pipe, [2, 1, 6, 5, 4, 3], current, state, self.endpoints)
        self.assertTrue(success)
        self.assertEqual(result, 'updated')
        self.assertEqual(timing['active_entries'], 6)
        self.assertEqual(current, [2, 1, 6, 5, 4, 3])
        success, result, timing = mapping_module.update_ocs_mapping(
            pipe, list(current), current, state, self.endpoints)
        self.assertTrue(success)
        self.assertEqual(result, 'unchanged')
        self.assertEqual(timing['programming_total_us'], 0)

    def test_failed_update_restores_previous_mapping(self):
        pipe = FakePipe()
        current = list(self.config['initial_mapping'])
        state = {'status': 1, 'mode': 'ocs', 'revision': 0}
        mapping_module.init_ocs_mapping(pipe, current, state, self.endpoints)
        original_entries = list(pipe.SwitchIngress.ocs_mapping.entries)
        pipe.SwitchIngress.ocs_mapping.fail_on_add = (
            self.endpoints[0]['dev_port'], self.endpoints[1]['dev_port'])
        success, detail, timing = mapping_module.update_ocs_mapping(
            pipe, [2, 1, 6, 5, 4, 3], current, state, self.endpoints)
        self.assertFalse(success)
        self.assertIsNone(timing)
        self.assertIn('restored', detail)
        self.assertEqual(state['status'], 1)
        self.assertEqual(current, self.config['initial_mapping'])
        self.assertEqual(pipe.SwitchIngress.ocs_mapping.entries, original_entries)

    def test_debug_mode_installs_full_mesh_and_restores_mapping(self):
        pipe = FakePipe()
        current = list(self.config['initial_mapping'])
        state = {'status': 1, 'mode': 'ocs', 'revision': 0}
        mapping_module.init_ocs_mapping(pipe, current, state, self.endpoints)

        success, result, timing = mapping_module.update_ocs_mode(
            pipe, 'debug', current, state, self.endpoints)
        self.assertTrue(success)
        self.assertEqual(result, 'updated')
        self.assertEqual(state['mode'], 'debug')
        self.assertEqual(timing['active_entries'], 30)
        self.assertEqual(len(pipe.SwitchIngress.ocs_mapping.entries), 30)

        success, detail, timing = mapping_module.update_ocs_mapping(
            pipe, [2, 1, 6, 5, 4, 3], current, state, self.endpoints)
        self.assertFalse(success)
        self.assertIn('debug mode', detail)
        self.assertIsNone(timing)

        success, result, timing = mapping_module.update_ocs_mode(
            pipe, 'ocs', current, state, self.endpoints)
        self.assertTrue(success)
        self.assertEqual(state['mode'], 'ocs')
        self.assertEqual(pipe.SwitchIngress.ocs_mapping.entries,
                         mapping_module.mapping_entries(current, self.endpoints))

    def test_delay_validation(self):
        self.assertEqual(mapping_module.validate_delay_ms(0), 0)
        self.assertEqual(mapping_module.validate_delay_ms(1000), 1000)
        for invalid in (-1, 1001, 1.5, True):
            with self.assertRaises(ValueError):
                mapping_module.validate_delay_ms(invalid)
        self.assertEqual(mapping_module.validate_delay_us(0), 0)
        self.assertEqual(mapping_module.validate_delay_us(1), 1)
        self.assertEqual(mapping_module.validate_delay_us(1000000), 1000000)
        for invalid in (-1, 1000001, 1.5, True):
            with self.assertRaises(ValueError):
                mapping_module.validate_delay_us(invalid)

    def test_request_delay_units(self):
        self.assertEqual(northbound_module.request_delay_us({}), 0)
        self.assertEqual(northbound_module.request_delay_us({'delay_ms': 10}), 10000)
        self.assertEqual(northbound_module.request_delay_us({'delay_us': 250}), 250)
        with self.assertRaises(ValueError):
            northbound_module.request_delay_us({'delay_ms': 1, 'delay_us': 1000})

    def test_http_handler_supports_low_latency_keep_alive(self):
        handler = northbound_module.OCSRequestHandler
        self.assertEqual(handler.protocol_version, 'HTTP/1.1')
        self.assertTrue(handler.disable_nagle_algorithm)


if __name__ == '__main__':
    unittest.main()
