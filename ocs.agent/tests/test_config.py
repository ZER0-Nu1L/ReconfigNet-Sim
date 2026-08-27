import json
import os
import sys
import tempfile
import unittest


PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_DIR not in sys.path:
    sys.path.insert(0, PROJECT_DIR)


from agent.config import (
    GO_SPLIT_GRPC,
    PYTHON_MONOLITH_HTTP_DIRECT,
    deployment_profile,
    load_agent_config,
)


class AgentConfigTests(unittest.TestCase):
    def test_canonical_profiles_have_fixed_boundaries(self):
        http = load_agent_config(os.path.join(
            PROJECT_DIR, 'config', 'p4app.json'))
        grpc = load_agent_config(os.path.join(
            PROJECT_DIR, 'config', 'p4app-go-split-grpc.json'))

        self.assertEqual(
            http['deployment_profile'], PYTHON_MONOLITH_HTTP_DIRECT)
        self.assertIn('http_api', http)
        self.assertNotIn('grpc_api', http)
        self.assertNotIn('worker', http)

        self.assertEqual(grpc['deployment_profile'], GO_SPLIT_GRPC)
        self.assertIn('grpc_api', grpc)
        self.assertIn('worker', grpc)
        self.assertNotIn('http_api', grpc)

    def test_deprecated_runtime_matrix_fields_have_migration_error(self):
        for field in (
                'agent_runtime', 'enable_rest_api', 'enable_grpc_api',
                'rest_api', 'device_worker'):
            with self.subTest(field=field):
                with self.assertRaisesRegex(
                        ValueError, 'deprecated OCS configuration fields'):
                    deployment_profile({
                        'deployment_profile': GO_SPLIT_GRPC,
                        field: {},
                    })

    def test_unknown_top_level_fields_are_rejected(self):
        source = os.path.join(PROJECT_DIR, 'config', 'p4app.json')
        with open(source, 'r') as file_obj:
            data = json.load(file_obj)
        data['unexpected'] = True
        data['model_file'] = os.path.join(
            PROJECT_DIR, 'config', 'ocs-model.yaml')
        data['capability_profile_file'] = os.path.join(
            PROJECT_DIR, 'config', 'p4app-capabilities.yaml')
        descriptor, path = tempfile.mkstemp(
            prefix='ocs-unknown-config-', suffix='.json')
        try:
            with os.fdopen(descriptor, 'w') as file_obj:
                json.dump(data, file_obj)
            with self.assertRaisesRegex(ValueError, 'unknown fields'):
                load_agent_config(path)
        finally:
            if os.path.exists(path):
                os.unlink(path)

    def test_tofino_http_rejects_bf_switchd_port(self):
        source = os.path.join(PROJECT_DIR, 'config', 'p4app.json')
        with open(source, 'r') as file_obj:
            data = json.load(file_obj)
        data['backend'] = {'type': 'bfrt'}
        data['model_file'] = os.path.join(
            PROJECT_DIR, 'config', 'ocs-model.yaml')
        data['capability_profile_file'] = os.path.join(
            PROJECT_DIR, 'config', 'p4app-capabilities.yaml')
        descriptor, path = tempfile.mkstemp(
            prefix='ocs-tofino-http-', suffix='.json')
        try:
            with os.fdopen(descriptor, 'w') as file_obj:
                json.dump(data, file_obj)
            with self.assertRaisesRegex(ValueError, 'must not use'):
                load_agent_config(path)
        finally:
            if os.path.exists(path):
                os.unlink(path)


if __name__ == '__main__':
    unittest.main()
