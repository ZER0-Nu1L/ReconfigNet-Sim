from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn
import json
import threading
import time

from config.custom_connect import validate_mapping
from p4util.ocs_map_p4runtime import (
    monotonic_ns,
    update_ocs_mapping,
    update_ocs_mode,
    validate_delay_ms,
    validate_delay_us,
)


def request_delay_us(data):
    if 'delay_ms' in data and 'delay_us' in data:
        raise ValueError('Specify only one of delay_ms or delay_us')
    if 'delay_us' in data:
        return validate_delay_us(data['delay_us'])
    delay_ms = data.get('delay_ms', 0)
    return validate_delay_ms(delay_ms) * 1000


class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


class OCSRequestHandler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    # Persistent control connections exchange small request/response messages.
    # Avoid the Nagle/delayed-ACK interaction that otherwise adds about 40 ms
    # between consecutive requests on an established connection.
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
            data = json.loads(self.rfile.read(content_length).decode('utf-8'))
        except (ValueError, UnicodeDecodeError):
            raise ValueError('Request body must be valid JSON')
        if not isinstance(data, dict):
            raise ValueError('Request body must be a JSON object')
        return data

    def do_GET(self):
        if self.path == '/ocs_mapping':
            self._write_json(self.server.mapping_snapshot())
            return
        if self.path == '/ocs_mode':
            self._write_json(self.server.mode_snapshot())
            return
        self._write_json({'error': 'Not found'}, 404)

    def do_POST(self):
        request_started_ns = monotonic_ns()
        try:
            request_started_unix_ns = time.time_ns()
        except AttributeError:
            request_started_unix_ns = int(time.time() * 1000000000)
        try:
            data = self._read_json()
            delay_us = request_delay_us(data)
            if self.path == '/ocs_mapping':
                if 'new_pi' not in data:
                    raise ValueError('Request body must contain new_pi')
                validate_mapping(data['new_pi'], self.server.num_hosts)
                success, detail, timing, conflict = self.server.update_mapping(
                    data['new_pi'], delay_us)
            elif self.path == '/ocs_mode':
                if 'mode' not in data:
                    raise ValueError('Request body must contain mode')
                success, detail, timing, conflict = self.server.update_mode(
                    data['mode'], delay_us)
            else:
                self._write_json({'error': 'Not found'}, 404)
                return
        except (ValueError, TypeError) as error:
            self._write_json({'error': str(error)}, 400)
            return

        payload = self.server.operation_snapshot()
        payload.update({
            'result': detail,
            'request_received_unix_ns': request_started_unix_ns,
            'request_id': self.server.request_id,
        })
        if timing is not None:
            response_timing = dict(timing)
            response_timing['server_total_us'] = (
                monotonic_ns() - request_started_ns) // 1000
            payload['timing'] = response_timing
        if success:
            payload['status'] = 'success'
            self._write_json(payload)
        else:
            payload['error'] = detail
            self._write_json(payload, 409 if conflict else 500)

    def log_message(self, format_string, *args):
        print("REST {} - {}".format(self.address_string(), format_string % args))


def run_rest_api(pi, runtime_state, p4_pipe, endpoints,
                 host='127.0.0.1', port=5000):
    class CustomServer(ThreadedHTTPServer):
        def __init__(self, *args, **kwargs):
            self.pi = pi
            self.runtime_state = runtime_state
            self.p4_pipe = p4_pipe
            self.endpoints = endpoints
            self.num_hosts = len(endpoints)
            self.update_lock = threading.Lock()
            self.request_id = 0
            ThreadedHTTPServer.__init__(self, *args, **kwargs)

        def state_name(self):
            status = self.runtime_state.get('status')
            if status == 1:
                return 'ready'
            if status == -1:
                return 'updating'
            return 'error'

        def operation_snapshot(self):
            state_name = self.state_name()
            return {
                'pi': list(self.pi),
                'mode': self.runtime_state.get('mode', 'ocs'),
                'status': state_name,
                'state': state_name,
                'revision': self.runtime_state.get('revision', 0),
            }

        def mapping_snapshot(self):
            with self.update_lock:
                return self.operation_snapshot()

        def mode_snapshot(self):
            with self.update_lock:
                snapshot = self.operation_snapshot()
                snapshot['active_entries'] = self.runtime_state.get(
                    'last_timing', {}).get('active_entries')
                return snapshot

        def _finish_request(self, success, detail, timing):
            self.request_id += 1
            if success and detail == 'updated':
                self.runtime_state['revision'] = (
                    self.runtime_state.get('revision', 0) + 1)
            return success, detail, timing

        def update_mapping(self, new_mapping, delay_us):
            with self.update_lock:
                if self.runtime_state.get('mode') != 'ocs':
                    self.request_id += 1
                    return (False,
                            'OCS mapping cannot be changed while debug mode is active',
                            None, True)
                success, detail, timing = update_ocs_mapping(
                    self.p4_pipe, new_mapping, self.pi,
                    self.runtime_state, self.endpoints, delay_us)
                success, detail, timing = self._finish_request(
                    success, detail, timing)
                return success, detail, timing, False

        def update_mode(self, mode, delay_us):
            with self.update_lock:
                success, detail, timing = update_ocs_mode(
                    self.p4_pipe, mode, self.pi,
                    self.runtime_state, self.endpoints, delay_us)
                success, detail, timing = self._finish_request(
                    success, detail, timing)
                return success, detail, timing, False

    server = CustomServer((host, port), OCSRequestHandler)
    print("Starting REST API on {}:{}".format(host, port))
    server.serve_forever()
