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

from config.device_profile import load_config, validate_config, validate_mapping
mapping_module = importlib.import_module('bfrt.ocs_mapping')


class FakeTable(object):
    def __init__(self):
        self.entries = []

    def clear(self):
        self.entries = []

    def add_with_permit_ocs(self, ingress_port, ucast_egress_port):
        self.entries.append((ingress_port, ucast_egress_port))


class FakePipe(object):
    def __init__(self):
        self.SwitchIngress = types.SimpleNamespace(ocs_mapping=FakeTable())


class OCSControlTests(unittest.TestCase):
    def setUp(self):
        config_path = os.path.join(
            NET_CTRL, 'config', 'device-profile.example.json')
        self.config = load_config(config_path)
        self.endpoints = self.config['endpoints']

    def test_example_profile_is_site_neutral(self):
        self.assertEqual(self.config['fabric'], 'example')
        self.assertEqual(self.config['num_hosts'], 6)
        self.assertEqual(self.config['initial_mapping'], [6, 3, 2, 5, 4, 1])
        self.assertTrue(all(
            endpoint['ipv4'].startswith('192.0.2.')
            for endpoint in self.config['endpoints']))

    def test_embedded_rest_configuration_is_rejected(self):
        config = dict(self.config)
        config['enable_rest_api'] = True
        with self.assertRaisesRegex(ValueError, 'no longer supported'):
            validate_config(config)

    def test_mapping_requires_symmetric_pairs(self):
        with self.assertRaises(ValueError):
            validate_mapping([2, 3, 1, 5, 6, 4], 6)
        with self.assertRaises(ValueError):
            validate_mapping([1, 2, 3, 4, 5, 6], 6)
        self.assertTrue(validate_mapping([2, 1, 6, 5, 4, 3], 6))

    def test_startup_mapping_initialization(self):
        pipe = FakePipe()
        current = list(self.config['initial_mapping'])
        state = {'status': 1, 'mode': 'ocs', 'revision': 0}
        timing = mapping_module.init_ocs_mapping(
            pipe, current, state, self.endpoints)
        self.assertEqual(timing['active_entries'], 6)
        self.assertEqual(state['status'], 1)
        self.assertEqual(state['mode'], 'ocs')
        self.assertEqual(pipe.SwitchIngress.ocs_mapping.entries,
                         mapping_module.mapping_entries(current, self.endpoints))


if __name__ == '__main__':
    unittest.main()
