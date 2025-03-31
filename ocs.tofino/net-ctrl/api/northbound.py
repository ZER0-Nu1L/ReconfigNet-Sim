from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn
import json, os
from p4util.ocs_map_p4runtime import init_ocs_mapping, update_ocs_mapping


class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    """HTTP servers that support multithreading"""


class OCSRequestHandler(BaseHTTPRequestHandler):
    """Request handler for the OCS northbound API"""

    def _set_headers(self, status=200):
        self.send_response(status)
        self.send_header('Content-type', 'application/json')
        self.send_header('Connection', 'close')
        self.end_headers()

    def do_GET(self):
        """handle GET request"""
        if self.path == '/ocs_mapping':
            self._handle_get_mapping()
        else:
            self._set_headers(404)
            self.wfile.write(json.dumps({'error': 'Not found'}).encode())

    def do_POST(self):
        """handle POST request"""
        if self.path == '/ocs_mapping':
            self._handle_update_mapping()
        else:
            self._set_headers(404)
            self.wfile.write(json.dumps({'error': 'Not found'}).encode())

    def _handle_get_mapping(self):
        """get current mapping"""
        response = {
            'pi': self.server.pi.copy(),
            'status': 'ready' if self.server.pi_state[0] == 1 else 'updating'
        }
        self._set_headers()
        self.wfile.write(json.dumps(response).encode())

    def _handle_update_mapping(self):
        """update current mapping"""
        content_length = int(self.headers.get('Content-Length', 0))
        post_data = self.rfile.read(content_length).decode('utf-8')

        try:
            data = json.loads(post_data)
            new_pi = data['new_pi']
        except (json.JSONDecodeError, KeyError) as e:
            self._set_headers(400)
            self.wfile.write(json.dumps({'error': 'Invalid request'}).encode())
            return

        if self.server.update_mapping(new_pi):
            self._set_headers()
            self.wfile.write(json.dumps({'status': 'success'}).encode())
        else:
            self._set_headers(409)
            self.wfile.write(json.dumps({'error': 'Update failed'}).encode())


def run_rest_api(pi, pi_state, p4_pipe, num_hosts, host='0.0.0.0', port=5000):
    """Start the REST API service for northbound"""
    class CustomServer(ThreadedHTTPServer):
        def __init__(self, *args, **kwargs):
            self.pi = pi
            self.pi_state = pi_state
            self.p4_pipe = p4_pipe
            self.num_hosts = num_hosts

            default_pi = [i + 1 if i % 2 == 1 else i - 1 for i in range(1, num_hosts + 1)]
            self.pi = default_pi
            if not init_ocs_mapping(self.p4_pipe, self.pi, self.pi_state, self.num_hosts):
                exit(1)
            
            super().__init__(*args, **kwargs)

        def update_mapping(self, new_pi):
            try:
                return update_ocs_mapping(
                    self.p4_pipe,
                    new_pi,
                    self.pi,
                    self.pi_state,
                    self.num_hosts
                )
            except Exception as e:
                print("Update error: {}".format(str(e)))
                return False

    server = CustomServer((host, port), OCSRequestHandler)
    print("Starting REST API on {}:{}".format(host, port))

    server.serve_forever()
