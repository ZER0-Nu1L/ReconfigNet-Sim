import os
import sys
import threading
import time

from agent.backend import (
    BackendTransitionError,
    monotonic_ns,
    validate_delay_us,
)
from agent.errors import UnsupportedError, ValidationError


DEFAULT_TABLE = 'SwitchIngress.ocs_mapping'
DEFAULT_INGRESS_FIELD = 'ig_intr_md.ingress_port'
DEFAULT_EGRESS_FIELD = 'ig_intr_tm_md.ucast_egress_port'
DEFAULT_ACTION = 'SwitchIngress.permit_ocs'


def unix_time_ns():
    try:
        return time.time_ns()
    except AttributeError:
        return int(time.time() * 1000000000)


def _load_bfrt_client(sde_install=None):
    sde_install = sde_install or os.environ.get('SDE_INSTALL')
    if sde_install:
        site_packages = os.path.join(
            sde_install, 'lib', 'python2.7', 'site-packages')
        tofino_packages = os.path.join(site_packages, 'tofino')
        for path in (site_packages, tofino_packages):
            if path not in sys.path:
                sys.path.insert(0, path)
    try:
        import bfrt_grpc.client as gc
    except ImportError as error:
        raise RuntimeError(
            'BF Runtime Python client is unavailable; set SDE_INSTALL or '
            'PYTHONPATH to the BF-SDE client packages: {}'.format(error))
    return gc


def _normalize_port_map(value):
    if not isinstance(value, dict) or not value:
        raise ValidationError(
            'backend.logical_to_device_port must be a non-empty object')
    result = {}
    device_ports = set()
    for logical, device in value.items():
        try:
            logical_port = int(logical)
        except (TypeError, ValueError):
            raise ValidationError(
                'logical port keys must be positive integers')
        if (logical_port < 1 or isinstance(device, bool) or
                not isinstance(device, int) or device < 0):
            raise ValidationError(
                'logical/device ports must be non-negative integers')
        if logical_port in result or device in device_ports:
            raise ValidationError(
                'logical_to_device_port must be one-to-one')
        result[logical_port] = device
        device_ports.add(device)
    expected = list(range(1, len(result) + 1))
    if sorted(result) != expected:
        raise ValidationError(
            'logical ports must be exactly 1..{}'.format(len(result)))
    return result


class BfrtBackend(object):
    """Backend for the BF Runtime gRPC server exposed by bf_switchd.

    The backend accepts logical directed port pairs. Device-specific dev_port
    values and BFRT names are kept entirely inside this adapter.
    """

    def __init__(self, config, consistency_mode='CACHED_ACK'):
        if consistency_mode not in (
                'CACHED_ACK', 'CACHED_SYNC', 'STRICT_DEVICE'):
            raise ValidationError(
                'consistency_mode must be CACHED_ACK, CACHED_SYNC, or '
                'STRICT_DEVICE')
        self.consistency_mode = consistency_mode
        self.write_verification = (
            'ACK' if consistency_mode == 'CACHED_ACK'
            else 'SOFTWARE_READBACK')
        self.readback_source = 'BFRT_SOFTWARE'
        self._logical_to_device = _normalize_port_map(
            config.get('logical_to_device_port'))
        self._device_to_logical = dict(
            (device, logical)
            for logical, device in self._logical_to_device.items())
        self._state_lock = threading.RLock()
        self._generation = 0
        self._cache_status = 'UNKNOWN'
        self._last_verified_unix_ns = 0
        self._last_reconcile_unix_ns = 0
        self._last_write_ack_unix_ns = 0
        self._drift_count = 0

        self.gc = _load_bfrt_client(config.get('sde_install'))
        notifications = self.gc.Notifications(
            enable_learn=False,
            enable_idletimeout=False,
            enable_port_status_change=False)
        self.interface = self.gc.ClientInterface(
            config.get('grpc_target', '127.0.0.1:50052'),
            client_id=config.get('client_id', 17),
            device_id=config.get('device_id', 0),
            is_master=False,
            notifications=notifications,
            timeout=config.get('timeout_seconds', 2),
            num_tries=config.get('subscribe_attempts', 5))
        self.p4_name = config.get('p4_name', 'ocs')
        self.interface.bind_pipeline_config(self.p4_name)
        self.info = self.interface.bfrt_info_get(self.p4_name)
        self.table = self.info.table_get(
            config.get('table_name', DEFAULT_TABLE))
        self.target = self.gc.Target(
            device_id=config.get('device_id', 0),
            pipe_id=config.get('pipe_id', 0xffff))
        self.ingress_field = config.get(
            'ingress_field', DEFAULT_INGRESS_FIELD)
        self.egress_field = config.get(
            'egress_field', DEFAULT_EGRESS_FIELD)
        self.action_name = config.get('action_name', DEFAULT_ACTION)

    def capabilities(self):
        return {
            'backend': 'tofino-bfrt-grpc',
            'readback': True,
            'transports': ['SEQUENTIAL', 'NATIVE_BATCH'],
            'native_batch': True,
            'dataplane_atomic': False,
            'write_verifications': [
                'ACK', 'SOFTWARE_READBACK', 'HARDWARE_READBACK'],
            'readback_sources': [
                'BFRT_SOFTWARE', 'BFRT_HARDWARE'],
        }

    def device_state(self):
        with self._state_lock:
            return {
                'generation': self._generation,
                'cache_status': self._cache_status,
                'last_verified_unix_ns': self._last_verified_unix_ns,
                'last_reconcile_unix_ns': self._last_reconcile_unix_ns,
                'last_write_ack_unix_ns': self._last_write_ack_unix_ns,
                'drift_count': self._drift_count,
                'write_verification': self.write_verification,
                'readback_source': self.readback_source,
            }

    def close(self):
        if self.interface is not None:
            self.interface._tear_down_stream()
            self.interface = None

    def _device_pair(self, logical_pair):
        try:
            return (
                self._logical_to_device[logical_pair[0]],
                self._logical_to_device[logical_pair[1]])
        except KeyError:
            raise ValidationError(
                'Unknown logical port pair {}'.format(logical_pair))

    def _logical_pair(self, device_pair):
        try:
            return (
                self._device_to_logical[device_pair[0]],
                self._device_to_logical[device_pair[1]])
        except KeyError:
            raise RuntimeError(
                'BFRT table contains an unmanaged device port pair {}'.format(
                    device_pair))

    def _key(self, logical_pair):
        ingress, egress = self._device_pair(logical_pair)
        return self.table.make_key([
            self.gc.KeyTuple(self.ingress_field, ingress),
            self.gc.KeyTuple(self.egress_field, egress),
        ])

    def _data(self):
        return self.table.make_data([], action_name=self.action_name)

    @staticmethod
    def _field_value(value):
        if isinstance(value, dict):
            return value.get('value')
        return value

    def read_pairs(self, source='SOFTWARE'):
        if source not in ('SOFTWARE', 'HARDWARE'):
            raise ValidationError(
                'readback source must be SOFTWARE or HARDWARE')
        from_hw = source == 'HARDWARE'
        rows = self.table.entry_get(
            self.target, None, {'from_hw': from_hw})
        result = set()
        for _, key in rows:
            fields = key.to_dict()
            device_pair = (
                self._field_value(fields[self.ingress_field]),
                self._field_value(fields[self.egress_field]))
            result.add(self._logical_pair(device_pair))
        now = unix_time_ns()
        with self._state_lock:
            self._cache_status = 'READY'
            self._last_verified_unix_ns = now
            self.readback_source = (
                'BFRT_HARDWARE' if from_hw else 'BFRT_SOFTWARE')
        return result

    def audit_hardware(self):
        started_ns = monotonic_ns()
        pairs = self.read_pairs('HARDWARE')
        return pairs, (monotonic_ns() - started_ns) // 1000

    def _write_pairs(self, operation, pairs, transport):
        ordered = sorted(pairs)
        if not ordered:
            return 0
        if transport == 'NATIVE_BATCH':
            keys = [self._key(pair) for pair in ordered]
            if operation == 'delete':
                self.table.entry_del(self.target, keys)
            else:
                self.table.entry_add(
                    self.target, keys,
                    [self._data() for _ in ordered])
            return 1
        if transport != 'SEQUENTIAL':
            raise UnsupportedError(
                'Unsupported BFRT transport {}'.format(transport))
        for pair in ordered:
            key = self._key(pair)
            if operation == 'delete':
                self.table.entry_del(self.target, [key])
            else:
                self.table.entry_add(
                    self.target, [key], [self._data()])
        return len(ordered)

    def _restore(self, previous_pairs):
        current = self.read_pairs('SOFTWARE')
        self._write_pairs(
            'delete', current - previous_pairs, 'NATIVE_BATCH')
        self._write_pairs(
            'insert', previous_pairs - current, 'NATIVE_BATCH')
        restored = self.read_pairs('SOFTWARE')
        if restored != previous_pairs:
            raise RuntimeError(
                'BFRT rollback readback mismatch: expected {}, got {}'.format(
                    sorted(previous_pairs), sorted(restored)))

    def apply(self, previous_pairs, target_pairs, strategy='FULL',
              delay_us=0, transport='SEQUENTIAL'):
        planning_started_ns = monotonic_ns()
        validate_delay_us(delay_us)
        if strategy not in ('FULL', 'DELTA'):
            raise ValidationError('strategy must be FULL or DELTA')
        if transport not in self.capabilities()['transports']:
            raise UnsupportedError(
                'Transport {} is not supported by BFRT'.format(transport))
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
            'write_verification': self.write_verification,
            'readback_source': (
                '' if self.write_verification == 'ACK'
                else 'BFRT_SOFTWARE'),
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
            self._last_write_ack_unix_ns = unix_time_ns()

            readback_started_ns = monotonic_ns()
            if self.write_verification == 'SOFTWARE_READBACK':
                observed = self.read_pairs('SOFTWARE')
                if observed != target_pairs:
                    raise RuntimeError(
                        'BFRT readback mismatch: expected {}, got {}'.format(
                            sorted(target_pairs), sorted(observed)))
            readback_finished_ns = monotonic_ns()
        except Exception as update_error:
            rollback_started_ns = monotonic_ns()
            try:
                self._restore(previous_pairs)
                rollback_error = None
            except Exception as error:
                rollback_error = error
                with self._state_lock:
                    self._cache_status = 'UNKNOWN'
            rollback_finished_ns = monotonic_ns()
            timing['rollback_us'] = (
                rollback_finished_ns - rollback_started_ns) // 1000
            timing['programming_total_us'] = (
                rollback_finished_ns - started_ns) // 1000
            raise BackendTransitionError(
                update_error, rollback_error, timing)

        with self._state_lock:
            self._generation += 1
            self._cache_status = 'READY'
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
