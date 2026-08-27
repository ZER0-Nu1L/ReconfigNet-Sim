from concurrent import futures
import json
import os
import resource
import threading

from ocs_agent.backends.base import BackendTransitionError, monotonic_ns


class DedicatedThreadBackend(object):
    """Run southbound calls on one persistent backend-owned thread."""

    def __init__(self, backend):
        self.backend = backend
        self._executor = futures.ThreadPoolExecutor(max_workers=1)

    def capabilities(self):
        return self.backend.capabilities()

    def _call(self, method_name, *args, **kwargs):
        method = getattr(self.backend, method_name)
        return self._executor.submit(method, *args, **kwargs).result()

    def _apply_on_executor(self, queued_ns, previous_pairs, target_pairs,
                           strategy, delay_us, transport):
        queue_wait_us = (monotonic_ns() - queued_ns) // 1000
        try:
            timing = self.backend.apply(
                previous_pairs, target_pairs, strategy,
                delay_us, transport)
        except BackendTransitionError as error:
            error.timing['southbound_queue_wait_us'] = queue_wait_us
            raise
        timing['southbound_queue_wait_us'] = queue_wait_us
        return timing

    def read_pairs(self):
        return self._call('read_pairs')

    def apply(self, previous_pairs, target_pairs, strategy='FULL',
              delay_us=0, transport='SEQUENTIAL'):
        queued_ns = monotonic_ns()
        return self._executor.submit(
            self._apply_on_executor, queued_ns,
            previous_pairs, target_pairs, strategy,
            delay_us, transport).result()

    def __getattr__(self, name):
        if name == 'device_state' and hasattr(self.backend, name):
            return lambda: self._call('device_state')
        if name == 'reconcile' and hasattr(self.backend, name):
            return lambda desired_pairs: self._call(
                'reconcile', desired_pairs)
        if name == 'recover' and hasattr(self.backend, name):
            return lambda desired_pairs, strategy='FULL', delay_us=0, \
                    transport='SEQUENTIAL': self._call(
                        'recover', desired_pairs, strategy,
                        delay_us, transport)
        raise AttributeError(name)

    def close(self):
        if hasattr(self.backend, 'close'):
            self._call('close')
        self._executor.shutdown(wait=True)


class ThreadDiagnosticsBackend(object):
    """Log per-thread southbound call costs without changing wire timing."""

    def __init__(self, backend, path, role):
        self.backend = backend
        self.path = path
        self.role = role
        self._write_lock = threading.Lock()

    @staticmethod
    def _usage():
        value = resource.getrusage(resource.RUSAGE_THREAD)
        return {
            'user_us': int(value.ru_utime * 1000000),
            'system_us': int(value.ru_stime * 1000000),
            'voluntary_context_switches': value.ru_nvcsw,
            'involuntary_context_switches': value.ru_nivcsw,
        }

    @staticmethod
    def _usage_delta(before, after):
        return dict(
            (name, after[name] - before[name]) for name in sorted(before))

    def _write(self, record):
        encoded = json.dumps(record, sort_keys=True) + '\n'
        with self._write_lock:
            with open(self.path, 'a') as file_obj:
                file_obj.write(encoded)

    def _call(self, method_name, *args, **kwargs):
        started_ns = monotonic_ns()
        started_usage = self._usage()
        thread = threading.current_thread()
        error_name = None
        try:
            return getattr(self.backend, method_name)(*args, **kwargs)
        except Exception as error:
            error_name = type(error).__name__
            raise
        finally:
            finished_usage = self._usage()
            record = {
                'pid': os.getpid(),
                'role': self.role,
                'method': method_name,
                'thread_ident': thread.ident,
                'thread_name': thread.name,
                'wall_us': (monotonic_ns() - started_ns) // 1000,
                'thread_usage': self._usage_delta(
                    started_usage, finished_usage),
                'error': error_name,
            }
            if method_name == 'apply' and len(args) >= 2:
                record.update({
                    'previous_entries': len(args[0]),
                    'target_entries': len(args[1]),
                    'strategy': kwargs.get(
                        'strategy', args[2] if len(args) > 2 else 'FULL'),
                    'transport': kwargs.get(
                        'transport', args[4] if len(args) > 4
                        else 'SEQUENTIAL'),
                })
            self._write(record)

    def capabilities(self):
        return self.backend.capabilities()

    def read_pairs(self):
        return self._call('read_pairs')

    def apply(self, previous_pairs, target_pairs, strategy='FULL',
              delay_us=0, transport='SEQUENTIAL'):
        return self._call(
            'apply', previous_pairs, target_pairs,
            strategy, delay_us, transport)

    def __getattr__(self, name):
        if name == 'device_state' and hasattr(self.backend, name):
            return lambda: self._call('device_state')
        if name == 'reconcile' and hasattr(self.backend, name):
            return lambda desired_pairs: self._call(
                'reconcile', desired_pairs)
        if name == 'recover' and hasattr(self.backend, name):
            return lambda desired_pairs, strategy='FULL', delay_us=0, \
                    transport='SEQUENTIAL': self._call(
                        'recover', desired_pairs, strategy,
                        delay_us, transport)
        raise AttributeError(name)

    def close(self):
        if hasattr(self.backend, 'close'):
            return self._call('close')


def dedicated_backend(backend, diagnostics_path=None):
    """Build the fixed executor used by the Go split profile."""
    if diagnostics_path:
        backend = ThreadDiagnosticsBackend(
            backend, diagnostics_path, 'device-call')
    backend = DedicatedThreadBackend(backend)
    if diagnostics_path:
        backend = ThreadDiagnosticsBackend(
            backend, diagnostics_path, 'agent-call')
    return backend
