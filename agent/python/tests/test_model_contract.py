import os
import re
import shutil
import subprocess
import sys
import unittest


PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AGENT_DIR = os.path.dirname(PROJECT_DIR)
REPOSITORY_DIR = os.path.dirname(AGENT_DIR)
if PROJECT_DIR not in sys.path:
    sys.path.insert(0, PROJECT_DIR)


from runtime.config import load_config


class ModelContractTests(unittest.TestCase):
    def setUp(self):
        self.config = load_config(os.path.join(
            AGENT_DIR, 'configs', 'p4app',
            'python-monolith-http-direct.json'))

    def test_model_capability_and_document_matrix_are_consistent(self):
        profile = self.config['capability_profile']
        self.assertEqual(self.config['profile'], profile['profile'])
        yaml_statuses = dict(
            (item['id'], item['status'])
            for item in profile['capabilities'])

        document_candidates = [
            os.path.join(
                REPOSITORY_DIR, 'docs',
                'ocs-model-support.md'),
            os.path.join(
                '/workspace/ReconfigNet-Sim', 'docs',
                'ocs-model-support.md'),
        ]
        document_path = next(
            (path for path in document_candidates if os.path.exists(path)),
            document_candidates[0])
        document_statuses = {}
        status_icons = {
            'SUPPORTED': '✅',
            'DERIVED': '🧮',
            'PLANNED': '🗓️',
            'UNSUPPORTED': '🚫',
            'OUT_OF_SCOPE': '➖',
        }
        with open(document_path, 'r', encoding='utf-8') as file_obj:
            for line in file_obj:
                if not line.startswith('| `'):
                    continue
                columns = [item.strip() for item in line.strip().split('|')]
                if len(columns) < 5:
                    continue
                capability_id = columns[1].strip('`')
                status_match = re.search(r'`([A-Z_]+)`', columns[3])
                if re.match(r'^[a-z0-9-]+$', capability_id):
                    self.assertIsNotNone(
                        status_match,
                        'Missing capability status for {}'.format(
                            capability_id))
                    status = status_match.group(1)
                    self.assertIn(status, status_icons)
                    self.assertTrue(
                        columns[3].startswith(status_icons[status]),
                        'Wrong status icon for {}'.format(capability_id))
                    document_statuses[capability_id] = status

        self.assertEqual(document_statuses, yaml_statuses)

    def test_gnmi_models_have_local_yang_modules(self):
        profile = self.config['capability_profile']
        local_models = set((
            'openconfig-platform',
            'oc-optical-switch',
            'oc-optical-switch-connections',
        ))
        advertised = set(
            item['name'] for item in profile['gnmi']['models'])
        self.assertTrue(local_models.issubset(advertised))
        for model_name in local_models:
            self.assertTrue(os.path.exists(os.path.join(
                AGENT_DIR, 'models', model_name + '.yang')))

    @unittest.skipUnless(shutil.which('pyang'), 'pyang is not installed')
    def test_yang_modules_validate_with_pyang(self):
        models_dir = os.path.join(AGENT_DIR, 'models')
        subprocess.check_call([
            shutil.which('pyang'),
            '-p', models_dir,
            os.path.join(models_dir, 'openconfig-platform.yang'),
            os.path.join(models_dir, 'oc-optical-switch.yang'),
            os.path.join(models_dir, 'oc-optical-switch-connections.yang'),
        ])


if __name__ == '__main__':
    unittest.main()
