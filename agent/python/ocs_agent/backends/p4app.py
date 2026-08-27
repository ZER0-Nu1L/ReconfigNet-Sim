from ocs_agent.backends.base import (
    BackendPreconditionError,
    BackendTransitionError,
    BackendUnavailableError,
    MAX_DELAY_US,
    monotonic_ns,
    validate_delay_us,
)
from ocs_agent.errors import UnsupportedError, ValidationError


TABLE_NAME = 'egress.ocs_mapping'
INGRESS_FIELD = 'standard_metadata.ingress_port'
EGRESS_FIELD = 'standard_metadata.egress_port'
ACTION_NAME = 'egress.permit_ocs'
def ocs_entry(ingress_port, egress_port):
    return {
        'table_name': TABLE_NAME,
        'match_fields': {
            INGRESS_FIELD: [ingress_port],
            EGRESS_FIELD: [egress_port],
        },
        'action_name': ACTION_NAME,
        'action_params': {},
    }


def _entry_pair(entry):
    fields = entry['match_fields']
    return (
        fields[INGRESS_FIELD][0],
        fields[EGRESS_FIELD][0],
    )


class P4AppBackend(object):
    def __init__(self, switch):
        self.switch = switch

    def capabilities(self):
        native_batch = bool(
            hasattr(self.switch, 'p4info_helper') and
            hasattr(self.switch, 'sw_conn') and
            hasattr(self.switch.sw_conn, 'client_stub'))
        return {
            'backend': 'p4app',
            'readback': bool(
                hasattr(self.switch, 'entries') or
                (hasattr(self.switch, 'sw_conn') and
                 hasattr(self.switch.sw_conn, 'ReadTableEntries'))),
            'transports': (
                ['SEQUENTIAL', 'NATIVE_BATCH'] if native_batch
                else ['SEQUENTIAL']),
            'native_batch': native_batch,
            'dataplane_atomic': False,
            'write_verifications': ['SOFTWARE_READBACK'],
            'readback_sources': ['P4RUNTIME'],
        }

    def _build_entry(self, pair):
        entry = ocs_entry(pair[0], pair[1])
        if (hasattr(self.switch, 'p4info_helper') and
                hasattr(self.switch, 'sw_conn')):
            return self.switch.p4info_helper.buildTableEntry(
                table_name=entry['table_name'],
                match_fields=entry['match_fields'],
                action_name=entry['action_name'],
                action_params=entry['action_params'])
        return entry

    def _insert_pair(self, pair):
        entry = self._build_entry(pair)
        if (hasattr(self.switch, 'p4info_helper') and
                hasattr(self.switch, 'sw_conn')):
            self.switch.sw_conn.WriteTableEntry(entry)
            return
        self.switch.insertTableEntry(entry)

    def _delete_pair(self, pair):
        entry = self._build_entry(pair)
        if (hasattr(self.switch, 'p4info_helper') and
                hasattr(self.switch, 'sw_conn')):
            self.switch.sw_conn.DeleteTableEntry(entry)
            return
        self.switch.removeTableEntry(entry)

    def _native_write(self, operation, pairs):
        if not pairs:
            return 0
        if not self.capabilities()['native_batch']:
            raise UnsupportedError(
                'P4app backend does not expose native batch writes')
        try:
            from p4.v1 import p4runtime_pb2
        except ImportError:
            raise UnsupportedError(
                'P4Runtime protobuf is unavailable for native batch writes')

        request = p4runtime_pb2.WriteRequest()
        request.device_id = self.switch.sw_conn.device_id
        request.election_id.low = 1
        update_type = (
            p4runtime_pb2.Update.DELETE if operation == 'delete'
            else p4runtime_pb2.Update.INSERT)
        for pair in pairs:
            update = request.updates.add()
            update.type = update_type
            update.entity.table_entry.CopyFrom(self._build_entry(pair))
        self.switch.sw_conn.client_stub.Write(request)
        return 1

    def _write_pairs(self, operation, pairs, transport):
        ordered = sorted(pairs)
        if transport == 'NATIVE_BATCH':
            return self._native_write(operation, ordered)
        if transport != 'SEQUENTIAL':
            raise UnsupportedError(
                'Unsupported backend transport {}'.format(transport))
        for pair in ordered:
            if operation == 'delete':
                self._delete_pair(pair)
            else:
                self._insert_pair(pair)
        return len(ordered)

    @staticmethod
    def _decode_exact(value):
        if isinstance(value, bytes):
            return int.from_bytes(value, byteorder='big')
        if isinstance(value, bytearray):
            return int.from_bytes(bytes(value), byteorder='big')
        return int(value)

    def read_pairs(self):
        if hasattr(self.switch, 'entries'):
            return set(_entry_pair(entry) for entry in self.switch.entries)
        if not (hasattr(self.switch, 'p4info_helper') and
                hasattr(self.switch, 'sw_conn') and
                hasattr(self.switch.sw_conn, 'ReadTableEntries')):
            raise UnsupportedError(
                'P4app backend does not support table readback')

        helper = self.switch.p4info_helper
        table_id = helper.get_tables_id(TABLE_NAME)
        pairs = set()
        for response in self.switch.sw_conn.ReadTableEntries(table_id):
            for entity in response.entities:
                table_entry = entity.table_entry
                if table_entry.table_id != table_id:
                    continue
                fields = {}
                for match in table_entry.match:
                    field_name = helper.get_match_field_name(
                        TABLE_NAME, match.field_id)
                    if match.WhichOneof('field_match_type') != 'exact':
                        continue
                    fields[field_name] = self._decode_exact(match.exact.value)
                if INGRESS_FIELD in fields and EGRESS_FIELD in fields:
                    pairs.add((fields[INGRESS_FIELD], fields[EGRESS_FIELD]))
        return pairs

    def _restore(self, previous_pairs):
        current = self.read_pairs()
        for pair in sorted(current - previous_pairs):
            self._delete_pair(pair)
        for pair in sorted(previous_pairs - current):
            self._insert_pair(pair)
        restored = self.read_pairs()
        if restored != previous_pairs:
            raise RuntimeError(
                'Rollback readback mismatch: expected {}, got {}'.format(
                    sorted(previous_pairs), sorted(restored)))

    def apply(self, previous_pairs, target_pairs, strategy='FULL',
              delay_us=0, transport='SEQUENTIAL'):
        planning_started_ns = monotonic_ns()
        validate_delay_us(delay_us)
        if strategy not in ('FULL', 'DELTA'):
            raise ValidationError('strategy must be FULL or DELTA')
        if transport not in self.capabilities()['transports']:
            raise UnsupportedError(
                'Transport {} is not supported by this backend'.format(
                    transport))

        previous_pairs = set(previous_pairs)
        target_pairs = set(target_pairs)
        if strategy == 'FULL':
            removed = set(previous_pairs)
            added = set(target_pairs)
            unchanged = set()
        else:
            removed = previous_pairs - target_pairs
            added = target_pairs - previous_pairs
            unchanged = previous_pairs & target_pairs
        planning_us = (monotonic_ns() - planning_started_ns) // 1000

        started_ns = monotonic_ns()
        timing = {
            'strategy': strategy,
            'transport': transport,
            'planning_us': planning_us,
            'delete_entries': len(removed),
            'insert_entries': len(added),
            'unchanged_entries': len(unchanged),
            'requested_gap_us': delay_us,
            'device_write_requests': 0,
            'write_verification': 'SOFTWARE_READBACK',
            'readback_source': 'P4RUNTIME',
        }
        try:
            delete_started_ns = monotonic_ns()
            timing['device_write_requests'] += self._write_pairs(
                'delete', removed, transport)
            delete_finished_ns = monotonic_ns()

            gap_started_ns = monotonic_ns()
            if delay_us:
                time.sleep(delay_us / 1000000.0)
            gap_finished_ns = monotonic_ns()

            install_started_ns = monotonic_ns()
            timing['device_write_requests'] += self._write_pairs(
                'insert', added, transport)
            install_finished_ns = monotonic_ns()

            readback_started_ns = monotonic_ns()
            observed = self.read_pairs()
            readback_finished_ns = monotonic_ns()
            if observed != target_pairs:
                raise RuntimeError(
                    'Readback mismatch: expected {}, got {}'.format(
                        sorted(target_pairs), sorted(observed)))
        except Exception as update_error:
            rollback_started_ns = monotonic_ns()
            try:
                self._restore(previous_pairs)
                rollback_error = None
            except Exception as error:
                rollback_error = error
            rollback_finished_ns = monotonic_ns()
            timing['rollback_us'] = (
                rollback_finished_ns - rollback_started_ns) // 1000
            timing['programming_total_us'] = (
                rollback_finished_ns - started_ns) // 1000
            raise BackendTransitionError(
                update_error, rollback_error, timing)

        timing.update({
            'delete_commit_us': (
                delete_finished_ns - delete_started_ns) // 1000,
            'actual_gap_us': (
                gap_finished_ns - gap_started_ns) // 1000,
            'install_commit_us': (
                install_finished_ns - install_started_ns) // 1000,
            'readback_us': (
                readback_finished_ns - readback_started_ns) // 1000,
            'rollback_us': 0,
            'active_entries': len(target_pairs),
            'programming_total_us': (
                readback_finished_ns - started_ns) // 1000,
        })
        return timing
