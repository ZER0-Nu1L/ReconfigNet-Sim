"""Low-latency HTTP NBI for the Python monolith deployment profile.

The handler shares the same model, lease, revision, validation, and rollback
semantics as the typed Go gRPC profile while keeping the backend in process.
"""

from __future__ import print_function

from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn
import json

from agent.backend import MAX_DELAY_US
from agent.errors import OcsError


def request_delay_us(data):
    if 'delay_ms' in data and 'delay_us' in data:
        raise ValueError('Specify only one of delay_ms or delay_us')
    if 'delay_us' in data:
        value = data['delay_us']
    else:
        delay_ms = data.get('delay_ms', 0)
        if isinstance(delay_ms, bool) or not isinstance(delay_ms, int):
            raise ValueError('delay_ms must be an integer')
        if not 0 <= delay_ms <= 1000:
            raise ValueError('delay_ms must be between 0 and 1000')
        value = delay_ms * 1000
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError('delay_us must be an integer')
    if not 0 <= value <= MAX_DELAY_US:
        raise ValueError(
            'delay_us must be between 0 and {}'.format(MAX_DELAY_US))
    return value


def _error_status(error):
    return {
        'INVALID_ARGUMENT': 400,
        'NOT_FOUND': 404,
        'FAILED_PRECONDITION': 409,
        'RESOURCE_EXHAUSTED': 429,
        'ABORTED': 409,
        'UNIMPLEMENTED': 501,
        'UNAVAILABLE': 503,
        'INTERNAL': 500,
    }.get(error.code, 500)


def _error_payload(error):
    return {
        'status': 'error',
        'error': error.message,
        'error_code': error.code,
        'details': error.details,
        'request_id': error.request_id,
        'timing': error.timing,
    }


class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


class OCSRequestHandler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    disable_nagle_algorithm = True

    def _write_json(self, payload, status=200):
        encoded = json.dumps(payload).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(encoded)))
        requested_connection = self.headers.get('Connection', '').lower()
        keep_alive = (
            requested_connection == 'keep-alive' or
            (self.request_version == 'HTTP/1.1' and
             requested_connection != 'close'))
        if keep_alive:
            self.send_header('Connection', 'keep-alive')
            self.close_connection = False
        else:
            self.send_header('Connection', 'close')
            self.close_connection = True
        self.end_headers()
        self.wfile.write(encoded)

    def _read_json(self):
        try:
            content_length = int(self.headers.get('Content-Length', 0))
        except ValueError:
            raise ValueError('Invalid Content-Length')
        if content_length <= 0 or content_length > 65536:
            raise ValueError('Request body must be between 1 and 65536 bytes')
        try:
            data = json.loads(
                self.rfile.read(content_length).decode('utf-8'))
        except (ValueError, UnicodeDecodeError):
            raise ValueError('Request body must be valid JSON')
        if not isinstance(data, dict):
            raise ValueError('Request body must be a JSON object')
        return data

    def do_GET(self):
        try:
            if self.path == '/ocs_control':
                self._write_json(self.server.agent.control_state())
                return
            if self.path == '/ocs_mapping':
                snapshot = self.server.agent.snapshot()
                snapshot['pi'] = self.server.agent.get_permutation()
                self._write_json(snapshot)
                return
            if self.path == '/ocs_mode':
                self._write_json(self.server.agent.snapshot())
                return
            self._write_json({'error': 'Not found'}, 404)
        except OcsError as error:
            self._write_json(_error_payload(error), _error_status(error))

    def do_POST(self):
        try:
            data = self._read_json()
            if self.path == '/ocs_control/acquire':
                self._write_json(self.server.agent.acquire_control(
                    data.get('client_id', ''),
                    data.get('requested_lease_seconds')))
                return
            if self.path == '/ocs_control/renew':
                self._write_json(self.server.agent.renew_control(
                    self._lease_token(data),
                    data.get('requested_lease_seconds')))
                return
            if self.path == '/ocs_control/release':
                self._write_json(self.server.agent.release_control(
                    self._lease_token(data)))
                return
            delay_us = request_delay_us(data)
            expected_revision = self._expected_revision()
            lease_token = self._lease_token(data)
            transport = data.get('transport', 'SEQUENTIAL')
            strategy = data.get('strategy', 'FULL')
            if not isinstance(transport, str):
                raise ValueError('transport must be a string')
            if not isinstance(strategy, str):
                raise ValueError('strategy must be a string')
            transport = transport.upper()
            strategy = strategy.upper()
            if self.path == '/ocs_mapping':
                if 'new_pi' not in data:
                    raise ValueError('Request body must contain new_pi')
                result = self.server.agent.apply_permutation(
                    data['new_pi'], strategy, transport, delay_us,
                    expected_revision, lease_token)
                result['pi'] = self.server.agent.get_permutation()
                self._write_json(result)
                return
            if self.path == '/ocs_mode':
                if 'mode' not in data:
                    raise ValueError('Request body must contain mode')
                result = self.server.agent.set_mode(
                    data['mode'], delay_us, transport, expected_revision,
                    lease_token)
                self._write_json(result)
                return
            if self.path == '/ocs_recover':
                if data.get('mode', 'REAPPLY_DESIRED') != 'REAPPLY_DESIRED':
                    raise ValueError('mode must be REAPPLY_DESIRED')
                result = self.server.agent.recover_device_state(
                    expected_revision, lease_token,
                    strategy, transport, delay_us)
                self._write_json(result)
                return
            self._write_json({'error': 'Not found'}, 404)
        except (ValueError, TypeError) as error:
            self._write_json({
                'status': 'error',
                'error': str(error),
                'error_code': 'INVALID_ARGUMENT',
            }, 400)
        except OcsError as error:
            self._write_json(_error_payload(error), _error_status(error))

    def _lease_token(self, data=None):
        token = self.headers.get('X-OCS-Control-Lease')
        if token:
            return token
        if data is not None:
            return data.get('lease_token')
        return None

    def _expected_revision(self):
        value = self.headers.get('X-OCS-Expected-Revision')
        if value is None:
            raise ValueError(
                'X-OCS-Expected-Revision header is required')
        try:
            revision = int(value)
        except (TypeError, ValueError):
            raise ValueError(
                'X-OCS-Expected-Revision must be a non-negative integer')
        if revision < 0:
            raise ValueError(
                'X-OCS-Expected-Revision must be a non-negative integer')
        return revision

    def log_message(self, format_string, *args):
        if self.server.access_log:
            print('REST {} - {}'.format(
                self.address_string(), format_string % args))


def create_rest_server(agent, host='127.0.0.1', port=5000,
                       access_log=False):
    class AgentHTTPServer(ThreadedHTTPServer):
        def __init__(self, *args, **kwargs):
            self.agent = agent
            self.access_log = access_log
            ThreadedHTTPServer.__init__(self, *args, **kwargs)

    return AgentHTTPServer((host, port), OCSRequestHandler)
