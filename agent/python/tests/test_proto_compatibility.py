"""Protocol boundaries shared by modern clients and legacy Device Workers."""

import importlib
import os
import subprocess
import sys
import unittest

from google.protobuf import __version__ as protobuf_version

from ocs_agent.proto import device_backend_pb2, ocs_operations_pb2


class ProtoCompatibilityTests(unittest.TestCase):
    def test_supported_runtime_selects_one_binding_directory(self):
        modern = int(protobuf_version.split('.')[0]) == 7
        for name in ('device_backend', 'ocs_operations', 'gnmi', 'gnmi_ext'):
            for suffix in ('_pb2', '_pb2_grpc'):
                module = importlib.import_module('ocs_agent.proto.' + name + suffix)
                self.assertEqual('modern' in module.__file__.split(os.sep), modern)

    def test_unsupported_runtime_fails_with_installation_guidance(self):
        script = (
            'import google.protobuf; google.protobuf.__version__ = "6.0.0"; '
            'import ocs_agent.proto')
        process = subprocess.Popen(
            [sys.executable, '-c', script], stdout=subprocess.PIPE,
            stderr=subprocess.PIPE)
        _, error = process.communicate()
        self.assertNotEqual(process.returncode, 0)
        self.assertIn(b'complete environment', error)

    def test_uint64_revision_and_explicit_zero_presence_round_trip(self):
        request = ocs_operations_pb2.ApplyBatchRequest(
            has_expected_revision=True, expected_revision=0,
            permutation=ocs_operations_pb2.Permutation(pi=[2, 1]),
            strategy=ocs_operations_pb2.EXECUTION_STRATEGY_DELTA)
        decoded = type(request).FromString(request.SerializeToString())
        self.assertTrue(decoded.has_expected_revision)
        self.assertEqual(decoded.expected_revision, 0)
        self.assertEqual(decoded.WhichOneof('intent'), 'permutation')
        reply = device_backend_pb2.ReadEntriesResponse(
            generation=(1 << 64) - 1, cache_status='READY')
        self.assertEqual(type(reply).FromString(
            reply.SerializeToString()).generation, (1 << 64) - 1)


if __name__ == '__main__':
    unittest.main()
