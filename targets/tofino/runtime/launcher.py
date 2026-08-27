#!/usr/bin/env python3
from __future__ import print_function

import argparse
import fcntl
import os
import signal
import subprocess
import sys
import threading


HERE = os.path.dirname(os.path.abspath(__file__))
REPOSITORY = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
AGENT_DIR = os.environ.get(
    'OCS_AGENT_DIR', os.path.join(REPOSITORY, 'agent', 'python'))
if AGENT_DIR in sys.path:
    sys.path.remove(AGENT_DIR)
sys.path.insert(0, AGENT_DIR)

from ocs_agent.backends.bfrt import BFRTBackend
from ocs_agent.config import (
    GO_SPLIT_GRPC,
    PYTHON_MONOLITH_HTTP_DIRECT,
    load_agent_config,
)
from ocs_agent.core import OcsAgent
from ocs_agent.device_worker import (
    cleanup_device_worker_target,
    create_device_worker_server,
)
from ocs_agent.execution import dedicated_backend
from ocs_agent.nbi.http import create_rest_server


def _acquire_ownership_lock(config):
    lock_path = config['backend'].get(
        'ownership_lock_file', '/tmp/ocs-agent-bfrt.lock')
    if not isinstance(lock_path, str) or not lock_path:
        raise RuntimeError(
            'backend.ownership_lock_file must be a non-empty string')
    lock_file = open(lock_path, 'a+')
    try:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (IOError, OSError):
        lock_file.close()
        raise RuntimeError(
            'Another OCS Agent already owns BFRT lock {}'.format(
                lock_path))
    lock_file.seek(0)
    lock_file.truncate()
    lock_file.write('{}\n'.format(os.getpid()))
    lock_file.flush()
    return lock_file


def run(config_path):
    config = load_agent_config(config_path)
    if config['backend']['type'] != 'bfrt':
        raise RuntimeError('Tofino Agent requires backend.type bfrt')

    ownership_lock = _acquire_ownership_lock(config)
    backend = None
    try:
        backend = BFRTBackend(
            config['backend'],
            consistency_mode=config['device']['consistency_mode'])
    except Exception:
        ownership_lock.close()
        raise

    stopping = threading.Event()
    previous_handlers = {}

    def stop_handler(signum, frame):
        stopping.set()

    for signum in (signal.SIGINT, signal.SIGTERM):
        previous_handlers[signum] = signal.signal(signum, stop_handler)

    http_server = None
    http_thread = None
    worker_server = None
    go_process = None
    agent = None
    try:
        profile = config['deployment_profile']
        if profile == PYTHON_MONOLITH_HTTP_DIRECT:
            agent = OcsAgent(
                config['model']['inventory'],
                config['model']['connections'], backend,
                config['model']['profile'],
                config['capability_profile'],
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
            worker_server = create_device_worker_server(
                backend, worker['target'],
                consistency_mode=config['device']['consistency_mode'])
            worker_server.start()
            print('Starting BFRT Device Worker on {}'.format(
                worker['target']))
            go_process = subprocess.Popen([
                config['go_agent']['binary'],
                '--config', config['path'],
            ], cwd=AGENT_DIR)
            print('Starting Go split gRPC Agent process {}'.format(
                go_process.pid))
        else:
            raise RuntimeError(
                'Unsupported deployment profile {}'.format(profile))

        while not stopping.wait(0.25):
            if go_process is not None and go_process.poll() is not None:
                raise RuntimeError(
                    'Go Agent exited with status {}'.format(
                        go_process.returncode))
    finally:
        if go_process is not None and go_process.poll() is None:
            go_process.terminate()
            try:
                go_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                go_process.kill()
                go_process.wait()
        if http_server is not None:
            http_server.shutdown()
            http_server.server_close()
        if http_thread is not None:
            http_thread.join(2)
        if agent is not None:
            agent.close()
        if worker_server is not None:
            worker_server.stop(2).wait()
            cleanup_device_worker_target(config['worker']['target'])
        try:
            if hasattr(backend, 'close'):
                backend.close()
        finally:
            ownership_lock.close()
        for signum, handler in previous_handlers.items():
            signal.signal(signum, handler)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description='Run an OCS Agent deployment profile on Tofino')
    parser.add_argument('--config', required=True)
    args = parser.parse_args(argv)
    run(args.config)


if __name__ == '__main__':
    main()
