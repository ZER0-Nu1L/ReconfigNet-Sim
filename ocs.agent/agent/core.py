import binascii
import os
import threading
import time

from agent.backend import (
    BackendPreconditionError,
    BackendTransitionError,
    BackendUnavailableError,
    monotonic_ns,
    validate_delay_us,
)
from agent.errors import (
    ApplyError,
    FailedPreconditionError,
    RevisionConflictError,
    ResourceExhaustedError,
    UnavailableError,
    ValidationError,
)
from agent.model import Connection, ConnectionSet


def unix_time_ns():
    try:
        return time.time_ns()
    except AttributeError:
        return int(time.time() * 1000000000)


class OcsAgent(object):
    def __init__(self, inventory, initial_connections, backend,
                 profile_name='p4app-v1', capability_profile=None,
                 consistency_mode='CACHED_SYNC', lease_seconds=30,
                 reconcile_interval_seconds=30,
                 startup_policy='REAPPLY_DESIRED'):
        if startup_policy not in ('REQUIRE_MATCH', 'REAPPLY_DESIRED'):
            raise ValidationError(
                'startup_policy must be REQUIRE_MATCH or REAPPLY_DESIRED')
        self.inventory = inventory
        self.backend = backend
        self.profile_name = profile_name
        self.capability_profile = capability_profile or {}
        self._lock = threading.RLock()
        self._connections = initial_connections
        self._mode = 'ocs'
        self._status = 'updating'
        self._revision = 0
        self._request_id = 0
        self._last_timing = None
        self._last_error = None
        self._consistency_mode = consistency_mode
        self._startup_policy = startup_policy
        self._startup_recovery_required = False
        self._lease_seconds = float(lease_seconds)
        self._lease_token = None
        self._lease_client_id = None
        self._lease_epoch = 0
        self._lease_expires_monotonic = 0
        self._lease_expires_unix_ns = 0
        self._device_generation = 0
        self._cache_status = 'UNKNOWN'
        self._last_verified_unix_ns = 0
        self._last_reconcile_unix_ns = 0
        self._drift_count = 0
        self._reconcile_interval_seconds = float(
            reconcile_interval_seconds)
        self._stop_reconcile = threading.Event()

        try:
            observed_pairs = self.backend.read_pairs()
            desired_pairs = initial_connections.directed_pairs()
            if observed_pairs == desired_pairs:
                timing = self._zero_timing(
                    'FULL', 'SEQUENTIAL', len(desired_pairs))
                timing['write_verification'] = 'STARTUP_MATCH'
                timing['readback_source'] = self.backend.capabilities().get(
                    'readback_sources', [''])[0]
            elif startup_policy == 'REQUIRE_MATCH':
                self._status = 'error'
                self._cache_status = 'DRIFTED'
                self._startup_recovery_required = True
                self._last_error = (
                    'Device state does not match YAML desired state')
                timing = self._zero_timing(
                    'FULL', 'SEQUENTIAL', len(observed_pairs))
                timing['write_verification'] = 'STARTUP_MISMATCH'
                timing['readback_source'] = self.backend.capabilities().get(
                    'readback_sources', [''])[0]
            else:
                timing = self.backend.apply(
                    observed_pairs, desired_pairs,
                    strategy='FULL', delay_us=0,
                    transport='SEQUENTIAL')
        except BackendTransitionError as error:
            self._status = 'error'
            raise
        if self._status != 'error':
            self._status = 'ready'
        self._last_timing = timing
        self._capture_backend_state(success=True)
        self._reconcile_thread = threading.Thread(
            target=self._reconcile_loop)
        self._reconcile_thread.daemon = True
        self._reconcile_thread.start()

    def close(self):
        self._stop_reconcile.set()
        if self._reconcile_thread.is_alive():
            self._reconcile_thread.join(1)

    def _capture_backend_state(self, success=False):
        if hasattr(self.backend, 'device_state'):
            state = self.backend.device_state()
            self._device_generation = state.get(
                'generation', self._device_generation)
            self._cache_status = state.get(
                'cache_status', self._cache_status)
            self._last_verified_unix_ns = state.get(
                'last_verified_unix_ns', self._last_verified_unix_ns)
            self._last_reconcile_unix_ns = state.get(
                'last_reconcile_unix_ns', self._last_reconcile_unix_ns)
            self._drift_count = state.get('drift_count', self._drift_count)
        elif success:
            self._device_generation += 1
            self._cache_status = 'READY'
            self._last_verified_unix_ns = unix_time_ns()
        if self._startup_recovery_required:
            # REQUIRE_MATCH is an administrative safety gate.  A backend
            # cache becoming READY, or a periodic read observing a match,
            # must not silently authorize writes after a startup mismatch.
            self._cache_status = 'DRIFTED'

    def _reconcile_loop(self):
        while not self._stop_reconcile.wait(
                self._reconcile_interval_seconds):
            try:
                self.reconcile_device_state()
            except Exception:
                pass

    def reconcile_device_state(self):
        with self._lock:
            desired = self._current_device_pairs()
            try:
                if hasattr(self.backend, 'reconcile'):
                    state = self.backend.reconcile(desired)
                    self._capture_backend_state()
                    observed = state.get('observed_entries', set())
                else:
                    observed = self.backend.read_pairs()
                    now = unix_time_ns()
                    self._last_reconcile_unix_ns = now
                    if observed == desired:
                        self._cache_status = 'READY'
                        self._last_verified_unix_ns = now
                    else:
                        if self._cache_status != 'DRIFTED':
                            self._drift_count += 1
                        self._cache_status = 'DRIFTED'
                if self._startup_recovery_required:
                    self._status = 'error'
                    self._cache_status = 'DRIFTED'
                    self._last_error = (
                        'Startup device mismatch requires explicit recovery')
                elif observed != desired or self._cache_status != 'READY':
                    self._status = 'error'
                    self._last_error = 'Device drift detected'
                elif self._last_error == 'Device drift detected':
                    self._status = 'ready'
                    self._last_error = None
                return self.device_state()
            except Exception as error:
                self._cache_status = 'UNKNOWN'
                self._status = 'error'
                self._last_error = str(error)
                return self.device_state()

    def device_state(self):
        state = {
            'consistency_mode': self._consistency_mode,
            'cache_status': self._cache_status,
            'generation': self._device_generation,
            'last_verified_unix_ns': self._last_verified_unix_ns,
            'last_reconcile_unix_ns': self._last_reconcile_unix_ns,
            'drift_count': self._drift_count,
            'startup_policy': self._startup_policy,
            'startup_recovery_required': self._startup_recovery_required,
        }
        if hasattr(self.backend, 'device_state'):
            backend_state = self.backend.device_state()
            for field in (
                    'write_verification', 'readback_source',
                    'last_write_ack_unix_ns'):
                state[field] = backend_state.get(field, '')
        return state

    def _expire_lease_locked(self):
        if (self._lease_token is not None and
                time.monotonic() >= self._lease_expires_monotonic):
            self._lease_token = None
            self._lease_client_id = None
            self._lease_expires_monotonic = 0
            self._lease_expires_unix_ns = 0

    def acquire_control(self, client_id='', requested_lease_seconds=None):
        with self._lock:
            self._expire_lease_locked()
            if self._lease_token is not None:
                raise ResourceExhaustedError(
                    'Control is already held by another writer', {
                        'client_id': self._lease_client_id,
                        'lease_epoch': self._lease_epoch,
                        'expires_unix_ns': self._lease_expires_unix_ns,
                    })
            duration = self._lease_duration(requested_lease_seconds)
            self._lease_epoch += 1
            self._lease_token = binascii.hexlify(os.urandom(32)).decode(
                'ascii')
            self._lease_client_id = client_id or ''
            self._set_lease_expiry(duration)
            return self._lease_reply_locked()

    def renew_control(self, lease_token, requested_lease_seconds=None):
        with self._lock:
            self._require_lease_locked(lease_token)
            self._set_lease_expiry(
                self._lease_duration(requested_lease_seconds))
            return self._lease_reply_locked()

    def release_control(self, lease_token):
        with self._lock:
            self._require_lease_locked(lease_token)
            self._lease_token = None
            self._lease_client_id = None
            self._lease_expires_monotonic = 0
            self._lease_expires_unix_ns = 0
            return self.control_state()

    def control_state(self):
        with self._lock:
            self._expire_lease_locked()
            return {
                'active': self._lease_token is not None,
                'client_id': self._lease_client_id or '',
                'lease_epoch': self._lease_epoch,
                'expires_unix_ns': self._lease_expires_unix_ns,
                'revision': self._revision,
            }

    def _lease_duration(self, requested):
        if requested in (None, 0):
            return self._lease_seconds
        if (isinstance(requested, bool) or
                not isinstance(requested, (int, float)) or requested <= 0):
            raise ValidationError(
                'requested_lease_seconds must be greater than zero')
        return min(float(requested), self._lease_seconds)

    def _set_lease_expiry(self, duration):
        self._lease_expires_monotonic = time.monotonic() + duration
        self._lease_expires_unix_ns = unix_time_ns() + int(
            duration * 1000000000)

    def _lease_reply_locked(self):
        return {
            'lease_token': self._lease_token,
            'lease_epoch': self._lease_epoch,
            'expires_unix_ns': self._lease_expires_unix_ns,
            'revision': self._revision,
        }

    def _require_lease_locked(self, lease_token):
        self._expire_lease_locked()
        if not lease_token or lease_token != self._lease_token:
            raise FailedPreconditionError(
                'A valid control lease is required', {
                    'lease_epoch': self._lease_epoch,
                })

    def _next_request_id(self):
        self._request_id += 1
        return self._request_id

    def _check_expected_revision(self, expected_revision):
        if expected_revision is None:
            raise FailedPreconditionError(
                'expected_revision is required for write operations')
        if (isinstance(expected_revision, bool) or
                not isinstance(expected_revision, int) or
                expected_revision < 0):
            raise ValidationError(
                'expected_revision must be a non-negative integer')
        if expected_revision != self._revision:
            raise RevisionConflictError(
                'Expected revision {} but current revision is {}'.format(
                    expected_revision, self._revision),
                {
                    'expected_revision': expected_revision,
                    'current_revision': self._revision,
                })

    def _check_write_preconditions(self, lease_token, expected_revision):
        self._require_lease_locked(lease_token)
        self._check_expected_revision(expected_revision)
        if self._startup_recovery_required:
            raise FailedPreconditionError(
                'Startup device mismatch requires RecoverDeviceState',
                self.device_state())
        if self._cache_status != 'READY':
            raise FailedPreconditionError(
                'Device cache is {}; recover device state first'.format(
                    self._cache_status), self.device_state())

    def _current_device_pairs(self):
        if self._mode == 'ocs':
            return self._connections.directed_pairs()
        return set(
            (source.index, destination.index)
            for source in self.inventory
            for destination in self.inventory
            if source.index != destination.index)

    def _zero_timing(self, strategy, transport, active_entries,
                     validation_us=0):
        return {
            'strategy': strategy,
            'transport': transport,
            'validation_us': validation_us,
            'planning_us': 0,
            'delete_entries': 0,
            'insert_entries': 0,
            'unchanged_entries': active_entries,
            'requested_gap_us': 0,
            'device_write_requests': 0,
            'delete_commit_us': 0,
            'actual_gap_us': 0,
            'install_commit_us': 0,
            'readback_us': 0,
            'precondition_readback_us': 0,
            'rollback_us': 0,
            'active_entries': active_entries,
            'programming_total_us': 0,
            'southbound_queue_wait_us': 0,
            'write_verification': '',
            'readback_source': '',
        }

    def _response(self, request_id, result, timing, received_unix_ns):
        return {
            'status': 'success',
            'result': result,
            'request_id': request_id,
            'request_received_unix_ns': received_unix_ns,
            'revision': self._revision,
            'state': self._status,
            'mode': self._mode,
            'active_entries': len(self._current_device_pairs()),
            'timing': timing,
        }

    def snapshot(self):
        with self._lock:
            return {
                'profile': self.profile_name,
                'status': self._status,
                'state': self._status,
                'mode': self._mode,
                'revision': self._revision,
                'request_id': self._request_id,
                'active_entries': len(self._current_device_pairs()),
                'connections': self._connections.as_list(
                    self._status, self._mode),
                'backend_capabilities': self.backend.capabilities(),
                'last_timing': self._last_timing,
                'last_error': self._last_error,
                'device_state': self.device_state(),
                'control_state': self.control_state(),
            }

    def openconfig_tree(self):
        with self._lock:
            return self._connections.openconfig_tree(
                self._status, self._mode)

    def get_connections(self):
        with self._lock:
            return ConnectionSet(self.inventory, list(self._connections))

    def get_permutation(self):
        with self._lock:
            if self._mode != 'ocs':
                raise FailedPreconditionError(
                    'Permutation is unavailable while debug mode is active')
            return self._connections.to_permutation()

    def _apply_target_locked(self, target, strategy, transport, delay_us,
                             expected_revision, received_unix_ns,
                             queue_wait_us, validation_us=0,
                             lease_token=None):
        validate_delay_us(delay_us)
        request_id = self._next_request_id()
        started_ns = monotonic_ns()
        try:
            precondition_started_ns = monotonic_ns()
            self._check_write_preconditions(
                lease_token, expected_revision)
            lease_revision_check_us = (
                monotonic_ns() - precondition_started_ns) // 1000
            if self._mode != 'ocs':
                raise FailedPreconditionError(
                    'Connections cannot be changed while debug mode is active')
            if target == self._connections:
                timing = self._zero_timing(
                    strategy, transport,
                    len(self._connections.directed_pairs()), validation_us)
                timing['queue_wait_us'] = queue_wait_us
                timing['lease_revision_check_us'] = lease_revision_check_us
                timing['cache_precondition_us'] = 0
                timing['server_total_us'] = (
                    (monotonic_ns() - started_ns) // 1000 +
                    queue_wait_us + validation_us)
                return self._response(
                    request_id, 'unchanged', timing, received_unix_ns)

            previous = self._connections
            self._status = 'updating'
            try:
                precondition_readback_us = 0
                if (self._consistency_mode == 'STRICT_DEVICE' and
                        not hasattr(self.backend, 'device_state')):
                    read_started_ns = monotonic_ns()
                    observed = self.backend.read_pairs()
                    precondition_readback_us = (
                        monotonic_ns() - read_started_ns) // 1000
                    if observed != previous.directed_pairs():
                        if self._cache_status != 'DRIFTED':
                            self._drift_count += 1
                        self._cache_status = 'DRIFTED'
                        self._status = 'error'
                        self._last_error = 'Device drift detected'
                        raise BackendPreconditionError(
                            'Device state does not match Agent cache',
                            self.device_state(), {
                                'precondition_readback_us':
                                    precondition_readback_us,
                            })
                timing = self.backend.apply(
                    previous.directed_pairs(), target.directed_pairs(),
                    strategy=strategy, delay_us=delay_us,
                    transport=transport)
                timing['precondition_readback_us'] = max(
                    timing.get('precondition_readback_us', 0),
                    precondition_readback_us)
            except BackendPreconditionError as error:
                self._capture_backend_state()
                self._status = 'error'
                self._last_error = str(error)
                failed = FailedPreconditionError(str(error), error.state)
                failed.request_id = request_id
                error.timing['queue_wait_us'] = queue_wait_us
                error.timing['validation_us'] = validation_us
                error.timing['lease_revision_check_us'] = (
                    lease_revision_check_us)
                error.timing['server_total_us'] = (
                    (monotonic_ns() - started_ns) // 1000 +
                    queue_wait_us + validation_us)
                failed.timing = error.timing
                raise failed
            except BackendUnavailableError as error:
                self._status = 'error'
                self._cache_status = 'UNKNOWN'
                self._last_error = str(error.error)
                unavailable = UnavailableError(
                    'Device worker is unavailable; device state is unknown',
                    {'backend': self.backend.capabilities()['backend']})
                unavailable.request_id = request_id
                error.timing['queue_wait_us'] = queue_wait_us
                error.timing['validation_us'] = validation_us
                error.timing['server_total_us'] = (
                    (monotonic_ns() - started_ns) // 1000 +
                    queue_wait_us + validation_us)
                unavailable.timing = error.timing
                raise unavailable
            except BackendTransitionError as error:
                self._capture_backend_state()
                self._status = 'ready' if error.restored else 'error'
                self._last_error = str(error.update_error)
                message = (
                    'Update failed and previous connections were restored: '
                    if error.restored else
                    'Update failed and rollback failed: ')
                if error.rollback_error is not None:
                    message += '{}; {}'.format(
                        error.update_error, error.rollback_error)
                else:
                    message += str(error.update_error)
                apply_error = ApplyError(
                    message, error.restored,
                    {'backend': self.backend.capabilities()['backend']})
                apply_error.request_id = request_id
                error.timing['queue_wait_us'] = queue_wait_us
                error.timing['validation_us'] = validation_us
                error.timing['server_total_us'] = (
                    (monotonic_ns() - started_ns) // 1000 +
                    queue_wait_us + validation_us)
                apply_error.timing = error.timing
                raise apply_error

            self._connections = target
            self._revision += 1
            self._capture_backend_state(success=True)
            self._status = 'ready'
            self._last_error = None
            timing['queue_wait_us'] = queue_wait_us
            timing['validation_us'] = validation_us
            timing['lease_revision_check_us'] = lease_revision_check_us
            timing.setdefault('cache_precondition_us', 0)
            timing['server_total_us'] = (
                (monotonic_ns() - started_ns) // 1000 +
                queue_wait_us + validation_us)
            self._last_timing = timing
            return self._response(
                request_id, 'updated', timing, received_unix_ns)
        except Exception as error:
            if hasattr(error, 'request_id') and error.request_id is None:
                error.request_id = request_id
            raise

    def _apply_target(self, target, strategy, transport, delay_us,
                      expected_revision, received_unix_ns,
                      validation_us=0, lease_token=None):
        queue_started_ns = monotonic_ns()
        with self._lock:
            queue_wait_us = (
                monotonic_ns() - queue_started_ns) // 1000
            return self._apply_target_locked(
                target, strategy, transport, delay_us,
                expected_revision, received_unix_ns, queue_wait_us,
                validation_us, lease_token)

    def replace_connection(self, connection, strategy='DELTA',
                           transport='SEQUENTIAL', delay_us=0,
                           expected_revision=None, lease_token=None):
        if not isinstance(connection, Connection):
            raise ValidationError('replace_connection requires a Connection')
        received_unix_ns = unix_time_ns()
        queue_started_ns = monotonic_ns()
        with self._lock:
            queue_wait_us = (
                monotonic_ns() - queue_started_ns) // 1000
            validation_started_ns = monotonic_ns()
            target = self._connections.replace(connection)
            validation_us = (
                monotonic_ns() - validation_started_ns) // 1000
            return self._apply_target_locked(
                target, strategy, transport, delay_us,
                expected_revision, received_unix_ns, queue_wait_us,
                validation_us, lease_token)

    def delete_connection(self, connection_name, strategy='DELTA',
                          transport='SEQUENTIAL', delay_us=0,
                          expected_revision=None, lease_token=None):
        received_unix_ns = unix_time_ns()
        queue_started_ns = monotonic_ns()
        with self._lock:
            queue_wait_us = (
                monotonic_ns() - queue_started_ns) // 1000
            validation_started_ns = monotonic_ns()
            target = self._connections.delete(connection_name)
            validation_us = (
                monotonic_ns() - validation_started_ns) // 1000
            return self._apply_target_locked(
                target, strategy, transport, delay_us,
                expected_revision, received_unix_ns, queue_wait_us,
                validation_us, lease_token)

    def replace_connections(self, connections, strategy='FULL',
                            transport='SEQUENTIAL', delay_us=0,
                            expected_revision=None, lease_token=None):
        validation_started_ns = monotonic_ns()
        if not isinstance(connections, ConnectionSet):
            raise ValidationError(
                'replace_connections requires a ConnectionSet')
        validation_us = (
            monotonic_ns() - validation_started_ns) // 1000
        return self._apply_target(
            connections, strategy, transport, delay_us,
            expected_revision, unix_time_ns(), validation_us, lease_token)

    def apply_connection_operations(self, operations, strategy='DELTA',
                                    transport='SEQUENTIAL', delay_us=0,
                                    expected_revision=None, lease_token=None):
        received_unix_ns = unix_time_ns()
        queue_started_ns = monotonic_ns()
        with self._lock:
            queue_wait_us = (
                monotonic_ns() - queue_started_ns) // 1000
            validation_started_ns = monotonic_ns()
            target = self._connections
            for operation in operations:
                if not isinstance(operation, tuple) or len(operation) != 2:
                    raise ValidationError(
                        'Connection operations must be (kind, value) tuples')
                kind, value = operation
                if kind == 'delete':
                    target = target.delete(value)
                elif kind == 'replace':
                    target = target.replace(value)
                elif kind == 'replace_all':
                    if not isinstance(value, ConnectionSet):
                        raise ValidationError(
                            'replace_all requires a ConnectionSet')
                    target = value
                else:
                    raise ValidationError(
                        'Unknown connection operation {}'.format(kind))
            validation_us = (
                monotonic_ns() - validation_started_ns) // 1000
            return self._apply_target_locked(
                target, strategy, transport, delay_us,
                expected_revision, received_unix_ns, queue_wait_us,
                validation_us, lease_token)

    def apply_permutation(self, mapping, strategy='FULL',
                          transport='SEQUENTIAL', delay_us=0,
                          expected_revision=None, lease_token=None):
        validation_started_ns = monotonic_ns()
        target = ConnectionSet.from_permutation(self.inventory, mapping)
        validation_us = (
            monotonic_ns() - validation_started_ns) // 1000
        return self._apply_target(
            target, strategy, transport, delay_us,
            expected_revision, unix_time_ns(), validation_us, lease_token)

    def set_mode(self, target_mode, delay_us=0,
                 transport='SEQUENTIAL', expected_revision=None,
                 lease_token=None):
        validation_started_ns = monotonic_ns()
        if target_mode not in ('ocs', 'debug'):
            raise ValidationError('mode must be either ocs or debug')
        validate_delay_us(delay_us)
        validation_us = (
            monotonic_ns() - validation_started_ns) // 1000
        received_unix_ns = unix_time_ns()
        queue_started_ns = monotonic_ns()
        with self._lock:
            queue_wait_us = (
                monotonic_ns() - queue_started_ns) // 1000
            request_id = self._next_request_id()
            started_ns = monotonic_ns()
            precondition_started_ns = monotonic_ns()
            self._check_write_preconditions(lease_token, expected_revision)
            lease_revision_check_us = (
                monotonic_ns() - precondition_started_ns) // 1000
            if target_mode == self._mode:
                timing = self._zero_timing(
                    'FULL', transport, len(self._current_device_pairs()),
                    validation_us)
                timing['queue_wait_us'] = queue_wait_us
                timing['lease_revision_check_us'] = lease_revision_check_us
                timing['cache_precondition_us'] = 0
                timing['server_total_us'] = (
                    (monotonic_ns() - started_ns) // 1000 +
                    queue_wait_us + validation_us)
                return self._response(
                    request_id, 'unchanged', timing, received_unix_ns)

            previous_pairs = self._current_device_pairs()
            if target_mode == 'ocs':
                target_pairs = self._connections.directed_pairs()
            else:
                target_pairs = set(
                    (source.index, destination.index)
                    for source in self.inventory
                    for destination in self.inventory
                    if source.index != destination.index)
            self._status = 'updating'
            try:
                precondition_readback_us = 0
                if (self._consistency_mode == 'STRICT_DEVICE' and
                        not hasattr(self.backend, 'device_state')):
                    read_started_ns = monotonic_ns()
                    observed = self.backend.read_pairs()
                    precondition_readback_us = (
                        monotonic_ns() - read_started_ns) // 1000
                    if observed != previous_pairs:
                        if self._cache_status != 'DRIFTED':
                            self._drift_count += 1
                        self._cache_status = 'DRIFTED'
                        raise BackendPreconditionError(
                            'Device state does not match Agent cache',
                            self.device_state(), {
                                'precondition_readback_us':
                                    precondition_readback_us,
                            })
                timing = self.backend.apply(
                    previous_pairs, target_pairs, strategy='FULL',
                    delay_us=delay_us, transport=transport)
                timing['precondition_readback_us'] = max(
                    timing.get('precondition_readback_us', 0),
                    precondition_readback_us)
            except BackendPreconditionError as error:
                self._capture_backend_state()
                self._status = 'error'
                self._last_error = str(error)
                failed = FailedPreconditionError(str(error), error.state)
                failed.request_id = request_id
                failed.timing = error.timing
                raise failed
            except BackendUnavailableError as error:
                self._status = 'error'
                self._cache_status = 'UNKNOWN'
                self._last_error = str(error.error)
                unavailable = UnavailableError(
                    'Device worker is unavailable; device state is unknown',
                    {'backend': self.backend.capabilities()['backend']})
                unavailable.request_id = request_id
                error.timing['queue_wait_us'] = queue_wait_us
                error.timing['validation_us'] = validation_us
                error.timing['server_total_us'] = (
                    (monotonic_ns() - started_ns) // 1000 +
                    queue_wait_us + validation_us)
                unavailable.timing = error.timing
                raise unavailable
            except BackendTransitionError as error:
                self._capture_backend_state()
                self._status = 'ready' if error.restored else 'error'
                apply_error = ApplyError(
                    'Mode update failed: {}'.format(error.update_error),
                    error.restored)
                apply_error.request_id = request_id
                error.timing['queue_wait_us'] = queue_wait_us
                error.timing['validation_us'] = validation_us
                error.timing['server_total_us'] = (
                    (monotonic_ns() - started_ns) // 1000 +
                    queue_wait_us + validation_us)
                apply_error.timing = error.timing
                raise apply_error

            self._mode = target_mode
            self._status = 'ready'
            self._revision += 1
            self._capture_backend_state(success=True)
            timing['queue_wait_us'] = queue_wait_us
            timing['validation_us'] = validation_us
            timing['lease_revision_check_us'] = lease_revision_check_us
            timing.setdefault('cache_precondition_us', 0)
            timing['server_total_us'] = (
                (monotonic_ns() - started_ns) // 1000 +
                queue_wait_us + validation_us)
            self._last_timing = timing
            return self._response(
                request_id, 'updated', timing, received_unix_ns)

    def recover_device_state(self, expected_revision=None,
                             lease_token=None, strategy='FULL',
                             transport='SEQUENTIAL', delay_us=0):
        received_unix_ns = unix_time_ns()
        queue_started_ns = monotonic_ns()
        with self._lock:
            queue_wait_us = (monotonic_ns() - queue_started_ns) // 1000
            request_id = self._next_request_id()
            started_ns = monotonic_ns()
            self._require_lease_locked(lease_token)
            self._check_expected_revision(expected_revision)
            desired = self._current_device_pairs()
            self._status = 'updating'
            try:
                if hasattr(self.backend, 'recover'):
                    timing = self.backend.recover(
                        desired, strategy, delay_us, transport)
                else:
                    read_started_ns = monotonic_ns()
                    observed = self.backend.read_pairs()
                    pre_read_us = (
                        monotonic_ns() - read_started_ns) // 1000
                    timing = self.backend.apply(
                        observed, desired, strategy=strategy,
                        delay_us=delay_us, transport=transport)
                    timing['precondition_readback_us'] = pre_read_us
                    verify_started_ns = monotonic_ns()
                    verified = self.backend.read_pairs()
                    verify_us = (
                        monotonic_ns() - verify_started_ns) // 1000
                    timing['readback_us'] = (
                        timing.get('readback_us', 0) + verify_us)
                    timing['programming_total_us'] = (
                        timing.get('programming_total_us', 0) + verify_us)
                    timing['write_verification'] = 'SOFTWARE_READBACK'
                    if verified != desired:
                        raise RuntimeError(
                            'Recovery readback does not match desired state')
            except Exception as error:
                self._cache_status = 'UNKNOWN'
                self._status = 'error'
                self._last_error = str(error)
                raise ApplyError(
                    'Device recovery failed: {}'.format(error), False)
            self._revision += 1
            self._startup_recovery_required = False
            self._capture_backend_state(success=True)
            self._status = 'ready'
            self._last_error = None
            timing['queue_wait_us'] = queue_wait_us
            timing['validation_us'] = 0
            timing['server_total_us'] = (
                monotonic_ns() - started_ns) // 1000 + queue_wait_us
            self._last_timing = timing
            return self._response(
                request_id, 'recovered', timing, received_unix_ns)
