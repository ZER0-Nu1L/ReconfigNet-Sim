#!/usr/bin/env python3
import os
import socket
import subprocess
import sys
import threading
import time


PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
SHARED_AGENT_DIR = os.environ.get('OCS_AGENT_DIR')
if not SHARED_AGENT_DIR:
    installed_agent = '/opt/ocs-agent/python'
    checkout_agent = os.path.abspath(os.path.join(
        PROJECT_DIR, '..', '..', 'agent', 'python'))
    SHARED_AGENT_DIR = (
        installed_agent if os.path.isdir(installed_agent)
        else checkout_agent)
if os.path.isdir(SHARED_AGENT_DIR):
    sys.path.insert(0, SHARED_AGENT_DIR)
AGENT_ROOT = os.path.dirname(SHARED_AGENT_DIR)

from mininet.cli import CLI
from mininet.log import setLogLevel
from p4app import P4Mininet, P4Program

from ocs_agent.backends.p4app import P4AppBackend
from ocs_agent.config import (
    GO_SPLIT_GRPC,
    PYTHON_MONOLITH_HTTP_DIRECT,
)
from ocs_agent.core import OcsAgent
from ocs_agent.device_worker import (
    cleanup_device_worker_target,
    create_device_worker_server,
)
from ocs_agent.execution import dedicated_backend
from ocs_agent.nbi.http import create_rest_server
from runtime.config import load_config
from runtime.topology import CustomTopo, setup_host_entries, switch_name
from runtime.l3_tables import setup_switch_basic_entries


def wait_for_go_agent(process, endpoint, timeout=10):
    deadline = time.time() + timeout
    host = endpoint['host']
    if host in ('0.0.0.0', '::'):
        host = '127.0.0.1'
    while time.time() < deadline:
        if process.poll() is not None:
            raise RuntimeError(
                'Go Agent exited with status {}'.format(
                    process.returncode))
        connection = None
        try:
            connection = socket.create_connection(
                (host, endpoint['port']), timeout=0.1)
            return
        except socket.error:
            time.sleep(0.05)
        finally:
            if connection is not None:
                connection.close()
    raise RuntimeError('Go Agent gRPC listener did not become ready')


def _config_path():
    return os.environ.get(
        'OCS_CONFIG_FILE',
        os.path.join(
            AGENT_ROOT, 'configs', 'p4app',
            'python-monolith-http-direct.json'))


def main():
    setLogLevel('info')
    if len(sys.argv) > 1 and sys.argv[1] == 'compile':
        P4Program('ocs.p4').compile()
        return

    config = load_config(_config_path())
    topo = CustomTopo(config['num_hosts'], config['mode'])
    net = P4Mininet(
        program='ocs.p4', topo=topo,
        enable_debugger=config['enable_debugger'])
    net.start()
    http_server = None
    http_thread = None
    device_worker_server = None
    go_process = None
    agent = None
    backend = None

    try:
        setup_host_entries(net, config['num_hosts'], config['mode'])
        setup_switch_basic_entries(net, config['num_hosts'])
        switch = net.get(switch_name)
        backend = P4AppBackend(switch)
        profile = config['deployment_profile']

        if profile == PYTHON_MONOLITH_HTTP_DIRECT:
            agent = OcsAgent(
                config['model']['inventory'],
                config['initial_connections'], backend,
                config['profile'], config['capability_profile'],
                config['device']['consistency_mode'],
                config['control']['lease_seconds'],
                config['control']['reconcile_interval_seconds'],
                config['startup_policy'])
            listener = config['http_api']
            http_server = create_rest_server(
                agent, listener['host'], listener['port'],
                listener['access_log'])
            http_thread = threading.Thread(
                target=http_server.serve_forever)
            http_thread.daemon = True
            http_thread.start()
            print(
                'Starting Python monolith HTTP/DIRECT Agent on {}:{}'.format(
                    listener['host'], http_server.server_port))
        elif profile == GO_SPLIT_GRPC:
            backend = dedicated_backend(
                backend,
                os.environ.get('OCS_THREAD_DIAGNOSTICS_FILE'))
            worker = config['worker']
            device_worker_server = create_device_worker_server(
                backend, worker['target'],
                consistency_mode=config['device']['consistency_mode'])
            device_worker_server.start()
            print('Starting Device Worker on {}'.format(worker['target']))
            go_process = subprocess.Popen([
                config['go_agent']['binary'],
                '--config', config['path'],
            ], cwd=PROJECT_DIR)
            print('Starting Go split gRPC Agent process {}'.format(
                go_process.pid))
            wait_for_go_agent(go_process, config['grpc_api'])
            print('Go split gRPC Agent listener is ready')
        else:
            raise RuntimeError(
                'Unsupported deployment profile {}'.format(profile))

        if config['enable_debugger']:
            container = os.environ['HOSTNAME']
            switch_log = '/tmp/p4app-logs/p4s.{}.log'.format(switch_name)
            print('---------------------------------------------------------')
            print('CLI: docker exec -t -i {} simple_switch_CLI'.format(
                container))
            print('Log: docker exec -t -i {} tail -f {}'.format(
                container, switch_log))
            print('Debugger: docker exec -t -i {} bm_p4dbg'.format(
                container))
            print('---------------------------------------------------------')

        CLI(net)
    finally:
        if http_server is not None:
            http_server.shutdown()
            http_server.server_close()
        if http_thread is not None:
            http_thread.join(2)
        if agent is not None:
            agent.close()
        if go_process is not None and go_process.poll() is None:
            go_process.terminate()
            try:
                go_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                go_process.kill()
                go_process.wait()
        if device_worker_server is not None:
            device_worker_server.stop(2).wait()
            cleanup_device_worker_target(config['worker']['target'])
        if backend is not None and hasattr(backend, 'close'):
            backend.close()
        net.stop()


if __name__ == '__main__':
    main()
