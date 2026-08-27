from concurrent import futures
import os
import threading
import time

import grpc

from agent.backend import BackendTransitionError
from api.proto import device_backend_pb2, device_backend_pb2_grpc


def monotonic_ns():
    try:
        return time.monotonic_ns()
    except AttributeError:
        return int(time.monotonic() * 1000000000)


def _pairs(messages):
    return set(
        (item.ingress_port, item.egress_port) for item in messages)


def _fill_pairs(target, pairs):
    for ingress, egress in sorted(pairs):
        item = target.add()
        item.ingress_port = ingress
        item.egress_port = egress


def _fill_timing(message, timing):
    timing = timing or {}
    for field in message.DESCRIPTOR.fields:
        if field.name in timing and timing[field.name] is not None:
            setattr(message, field.name, timing[field.name])


class DeviceBackendService(
        device_backend_pb2_grpc.DeviceBackendServicer):
    def __init__(self, backend, consistency_mode='CACHED_SYNC'):
        if consistency_mode not in (
                'CACHED_ACK', 'CACHED_SYNC', 'STRICT_DEVICE'):
            raise ValueError(
                'consistency_mode must be CACHED_ACK, CACHED_SYNC, or '
                'STRICT_DEVICE')
        self.backend = backend
        self.consistency_mode = consistency_mode
        capabilities = self.backend.capabilities()
        self._write_verification = (
            'ACK' if consistency_mode == 'CACHED_ACK'
            else 'SOFTWARE_READBACK')
        self._readback_source = (
            capabilities.get('readback_sources') or [''])[0]
        self._lock = threading.RLock()
        self._entries = set(self.backend.read_pairs())
        self._generation = 1
        self._cache_status = 'READY'
        self._last_verified_unix_ns = self._unix_ns()
        self._last_reconcile_unix_ns = 0
        self._last_write_ack_unix_ns = 0
        self._drift_count = 0

    @staticmethod
    def _unix_ns():
        try:
            return time.time_ns()
        except AttributeError:
            return int(time.time() * 1000000000)

    def _fill_state(self, response, entries=None):
        if hasattr(response, 'entries'):
            _fill_pairs(
                response.entries,
                self._entries if entries is None else entries)
        if hasattr(response, 'observed_entries'):
            _fill_pairs(
                response.observed_entries,
                self._entries if entries is None else entries)
        response.generation = self._generation
        response.cache_status = self._cache_status
        response.last_verified_unix_ns = self._last_verified_unix_ns
        response.last_reconcile_unix_ns = self._last_reconcile_unix_ns
        response.drift_count = self._drift_count
        response.write_verification = self._write_verification
        response.readback_source = self._readback_source
        response.last_write_ack_unix_ns = self._last_write_ack_unix_ns
        return response

    def _mark_unknown(self):
        self._cache_status = 'UNKNOWN'

    def _mark_drift(self):
        if self._cache_status != 'DRIFTED':
            self._drift_count += 1
        self._cache_status = 'DRIFTED'

    def Capabilities(self, request, context):
        capabilities = self.backend.capabilities()
        return device_backend_pb2.BackendCapabilities(
            backend=capabilities['backend'],
            readback=capabilities['readback'],
            native_batch=capabilities['native_batch'],
            dataplane_atomic=capabilities['dataplane_atomic'],
            transports=capabilities['transports'],
            write_verifications=capabilities.get(
                'write_verifications', []),
            readback_sources=capabilities.get('readback_sources', []))

    def ReadEntries(self, request, context):
        with self._lock:
            return self._fill_state(
                device_backend_pb2.ReadEntriesResponse())

    def ApplyTransition(self, request, context):
        started_ns = monotonic_ns()
        response = device_backend_pb2.ApplyTransitionResponse()
        expected = _pairs(request.expected_entries)
        target = _pairs(request.target_entries)
        with self._lock:
            cache_check_started_ns = monotonic_ns()
            response.timing.cache_precondition_us = (
                monotonic_ns() - cache_check_started_ns) // 1000
            if self._cache_status != 'READY':
                response.success = False
                response.restored = False
                response.error_code = 'FAILED_PRECONDITION'
                response.error = 'Device cache is {}'.format(
                    self._cache_status)
                response.timing.device_worker_total_us = (
                    monotonic_ns() - started_ns) // 1000
                return self._fill_state(response)
            if request.expected_generation != self._generation:
                response.success = False
                response.restored = True
                response.error_code = 'FAILED_PRECONDITION'
                response.error = (
                    'Device generation does not match expected generation')
                response.timing.device_worker_total_us = (
                    monotonic_ns() - started_ns) // 1000
                return self._fill_state(response)
            if expected != self._entries:
                response.success = False
                response.restored = True
                response.error_code = 'FAILED_PRECONDITION'
                response.error = 'Core state does not match Worker cache'
                response.timing.device_worker_total_us = (
                    monotonic_ns() - started_ns) // 1000
                return self._fill_state(response)

            previous = set(self._entries)
            if self.consistency_mode == 'STRICT_DEVICE':
                precondition_started_ns = monotonic_ns()
                observed = self.backend.read_pairs()
                response.timing.precondition_readback_us = (
                    monotonic_ns() - precondition_started_ns) // 1000
                if observed != previous:
                    self._mark_drift()
                    self._last_reconcile_unix_ns = self._unix_ns()
                    response.success = False
                    response.restored = False
                    response.error_code = 'FAILED_PRECONDITION'
                    response.error = (
                        'Device state does not match Worker cache')
                    response.timing.device_worker_total_us = (
                        monotonic_ns() - started_ns) // 1000
                    return self._fill_state(response, observed)

            try:
                timing = self.backend.apply(
                    previous, target, strategy=request.strategy,
                    delay_us=request.delay_us, transport=request.transport)
                response.success = True
                response.restored = True
                self._entries = set(target)
                self._generation += 1
                self._cache_status = 'READY'
                now = self._unix_ns()
                self._last_write_ack_unix_ns = now
                self._write_verification = timing.get(
                    'write_verification', self._write_verification)
                if timing.get('readback_source'):
                    self._readback_source = timing['readback_source']
                if self._write_verification != 'ACK':
                    self._last_verified_unix_ns = now
                _fill_timing(response.timing, timing)
            except BackendTransitionError as error:
                response.success = False
                response.restored = error.restored
                response.error_code = (
                    'ABORTED' if error.restored else 'INTERNAL')
                response.error = str(error.update_error)
                if error.restored:
                    self._entries = previous
                    self._cache_status = 'READY'
                    self._last_verified_unix_ns = self._unix_ns()
                else:
                    self._mark_unknown()
                if error.rollback_error is not None:
                    response.rollback_error = str(error.rollback_error)
                _fill_timing(response.timing, error.timing)
            except Exception as error:
                response.success = False
                response.restored = False
                response.error_code = 'INTERNAL'
                response.error = str(error)
                self._mark_unknown()
            response.timing.device_worker_total_us = (
                monotonic_ns() - started_ns) // 1000
            return self._fill_state(response)

    def Reconcile(self, request, context):
        desired = _pairs(request.desired_entries)
        with self._lock:
            observed = self.backend.read_pairs()
            now = self._unix_ns()
            self._last_reconcile_unix_ns = now
            self._last_verified_unix_ns = now
            if observed == self._entries and desired == self._entries:
                self._cache_status = 'READY'
            else:
                self._mark_drift()
            return self._fill_state(
                device_backend_pb2.ReadEntriesResponse(), observed)

    def Recover(self, request, context):
        started_ns = monotonic_ns()
        response = device_backend_pb2.ApplyTransitionResponse()
        desired = _pairs(request.desired_entries)
        with self._lock:
            try:
                precondition_started_ns = monotonic_ns()
                observed = self.backend.read_pairs()
                response.timing.precondition_readback_us = (
                    monotonic_ns() - precondition_started_ns) // 1000
                timing = self.backend.apply(
                    observed, desired, strategy=request.strategy or 'FULL',
                    delay_us=request.delay_us,
                    transport=request.transport or 'SEQUENTIAL')
                verify_started_ns = monotonic_ns()
                verified = self.backend.read_pairs()
                verify_us = (monotonic_ns() - verify_started_ns) // 1000
                timing['readback_us'] = (
                    timing.get('readback_us', 0) + verify_us)
                timing['programming_total_us'] = (
                    timing.get('programming_total_us', 0) + verify_us)
                timing['write_verification'] = 'SOFTWARE_READBACK'
                if verified != desired:
                    raise RuntimeError(
                        'Recovery readback does not match desired state')
                _fill_timing(response.timing, timing)
                self._entries = set(desired)
                self._generation += 1
                self._cache_status = 'READY'
                now = self._unix_ns()
                self._last_write_ack_unix_ns = now
                self._last_verified_unix_ns = now
                self._last_reconcile_unix_ns = now
                response.success = True
                response.restored = True
            except BackendTransitionError as error:
                response.success = False
                response.restored = error.restored
                response.error = str(error.update_error)
                response.error_code = (
                    'ABORTED' if error.restored else 'INTERNAL')
                if error.rollback_error is not None:
                    response.rollback_error = str(error.rollback_error)
                _fill_timing(response.timing, error.timing)
                self._mark_drift() if error.restored else self._mark_unknown()
            except Exception as error:
                response.success = False
                response.restored = False
                response.error_code = 'INTERNAL'
                response.error = str(error)
                self._mark_unknown()
            response.timing.device_worker_total_us = (
                monotonic_ns() - started_ns) // 1000
            return self._fill_state(response)


def _socket_path(target):
    for prefix in ('unix://', 'unix:'):
        if target.startswith(prefix):
            return target[len(prefix):]
    return None


def create_device_worker_server(backend, target, max_workers=4,
                                consistency_mode='CACHED_SYNC'):
    socket_path = _socket_path(target)
    if socket_path and os.path.exists(socket_path):
        os.unlink(socket_path)
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=max_workers))
    service = DeviceBackendService(backend, consistency_mode)
    device_backend_pb2_grpc.add_DeviceBackendServicer_to_server(
        service, server)
    server.ocs_device_service = service
    if not server.add_insecure_port(target):
        raise RuntimeError(
            'Unable to bind Device Worker on {}'.format(target))
    return server


def cleanup_device_worker_target(target):
    socket_path = _socket_path(target)
    if socket_path and os.path.exists(socket_path):
        os.unlink(socket_path)
