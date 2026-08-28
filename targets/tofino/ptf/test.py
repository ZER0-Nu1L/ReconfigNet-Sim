"""PTF coverage for OCS forwarding and production BFRT reconfiguration."""

import json

import ptf
from ptf.base_tests import BaseTest
import ptf.testutils as testutils

from ocs_agent.backends.bfrt import BFRTBackend


INITIAL_MAPPING = [6, 3, 2, 5, 4, 1]
UPDATED_MAPPING = [2, 1, 4, 3, 6, 5]
LOGICAL_TO_DEVICE_PORT = dict((port, port) for port in range(1, 7))
ENDPOINT_IP = dict(
    (port, '192.0.2.{}'.format(port)) for port in range(1, 7))
ENDPOINT_MAC = dict(
    (port, '02:00:00:00:00:{:02x}'.format(port)) for port in range(1, 7))


def mapping_pairs(mapping):
    return set(
        (source, destination)
        for source, destination in enumerate(mapping, start=1))


class OCSMappingReconfigurationTest(BaseTest):
    def setUp(self):
        BaseTest.setUp(self)
        self.dataplane = ptf.dataplane_instance
        self.dataplane.flush()

    def tearDown(self):
        self.dataplane.flush()
        BaseTest.tearDown(self)

    def _packets(self, source, destination):
        packet_id = source * 256 + destination
        packet = testutils.simple_tcp_packet(
            pktlen=128,
            eth_dst='00:11:22:33:44:55',
            eth_src=ENDPOINT_MAC[source],
            ip_src=ENDPOINT_IP[source],
            ip_dst=ENDPOINT_IP[destination],
            ip_ttl=64,
            ip_id=packet_id,
            tcp_sport=10000 + source,
            tcp_dport=20000 + destination,
        )
        expected = testutils.simple_tcp_packet(
            pktlen=128,
            eth_dst=ENDPOINT_MAC[destination],
            eth_src=ENDPOINT_MAC[source],
            ip_src=ENDPOINT_IP[source],
            ip_dst=ENDPOINT_IP[destination],
            ip_ttl=63,
            ip_id=packet_id,
            tcp_sport=10000 + source,
            tcp_dport=20000 + destination,
        )
        return packet, expected

    def _verify_permitted(self, pairs):
        for source, destination in sorted(pairs):
            packet, expected = self._packets(source, destination)
            testutils.send_packet(self, source, packet)
            testutils.verify_packet(self, expected, destination)
            testutils.verify_no_other_packets(self, timeout=0.1)

    def _verify_dropped(self, pairs):
        for source, destination in sorted(pairs):
            packet, unused_expected = self._packets(source, destination)
            testutils.send_packet(self, source, packet)
            testutils.verify_no_other_packets(self, timeout=0.2)

    def runTest(self):
        initial_pairs = mapping_pairs(INITIAL_MAPPING)
        updated_pairs = mapping_pairs(UPDATED_MAPPING)
        non_self_pairs = set(
            (source, destination)
            for source in range(1, 7)
            for destination in range(1, 7)
            if source != destination)
        initial_drops = non_self_pairs - initial_pairs
        self.assertEqual(len(initial_pairs), 6)
        self.assertEqual(len(initial_drops), 24)
        self.assertEqual(len(updated_pairs), 6)
        self.assertFalse(initial_pairs & updated_pairs)

        timing_file = testutils.test_param_get('timing_file')
        self.assertTrue(timing_file, 'timing_file test parameter is required')
        evidence = {
            'initial_mapping': INITIAL_MAPPING,
            'updated_mapping': UPDATED_MAPPING,
            'initial_permitted_paths': len(initial_pairs),
            'initial_dropped_paths': len(initial_drops),
            'updated_permitted_paths': len(updated_pairs),
            'retired_paths_dropped': len(initial_pairs),
        }
        backend = None
        try:
            backend = BFRTBackend({
                'grpc_target': '127.0.0.1:50052',
                'client_id': 41,
                'device_id': 0,
                'p4_name': 'ocs',
                'logical_to_device_port': LOGICAL_TO_DEVICE_PORT,
                'timeout_seconds': 10,
                'subscribe_attempts': 5,
            }, consistency_mode='CACHED_SYNC')

            observed_initial = backend.read_pairs('SOFTWARE')
            evidence['initial_readback'] = sorted(observed_initial)
            self.assertEqual(observed_initial, initial_pairs)

            self.dataplane.flush()
            self._verify_permitted(initial_pairs)
            self._verify_dropped(initial_drops)

            evidence['reconfiguration_timing'] = backend.apply(
                initial_pairs,
                updated_pairs,
                strategy='DELTA',
                transport='NATIVE_BATCH')
            observed_updated = backend.read_pairs('SOFTWARE')
            evidence['updated_readback'] = sorted(observed_updated)
            self.assertEqual(observed_updated, updated_pairs)

            self.dataplane.flush()
            self._verify_permitted(updated_pairs)
            self._verify_dropped(initial_pairs)
        finally:
            if backend is not None:
                try:
                    current_pairs = backend.read_pairs('SOFTWARE')
                    if current_pairs != initial_pairs:
                        evidence['restore_timing'] = backend.apply(
                            current_pairs,
                            initial_pairs,
                            strategy='DELTA',
                            transport='NATIVE_BATCH')
                    restored_pairs = backend.read_pairs('SOFTWARE')
                    evidence['restored_readback'] = sorted(restored_pairs)
                    self.assertEqual(restored_pairs, initial_pairs)
                finally:
                    backend.close()
            if timing_file:
                with open(timing_file, 'w', encoding='utf-8') as output:
                    json.dump(evidence, output, indent=2, sort_keys=True)
                    output.write('\n')
