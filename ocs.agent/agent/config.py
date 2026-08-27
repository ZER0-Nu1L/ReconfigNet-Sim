import json
import os

from agent.profile import load_capability_profile, load_model


PYTHON_MONOLITH_HTTP_DIRECT = 'python-monolith-http-direct'
GO_SPLIT_GRPC = 'go-split-grpc'
SUPPORTED_DEPLOYMENT_PROFILES = (
    PYTHON_MONOLITH_HTTP_DIRECT,
    GO_SPLIT_GRPC,
)
DEPRECATED_CONFIG_FIELDS = (
    'agent_runtime',
    'enable_rest_api',
    'enable_grpc_api',
    'rest_api',
    'device_worker',
)
COMMON_CONFIG_FIELDS = frozenset((
    'deployment_profile',
    'model_file',
    'capability_profile_file',
    'mode',
    'enable_debugger',
    'device',
    'startup_policy',
    'control',
    'backend',
))


def _listener(raw, name, default_port, access_log=False):
    value = raw.get(name, {})
    if not isinstance(value, dict):
        raise ValueError('{} must be an object'.format(name))
    allowed = set(('host', 'port'))
    if access_log:
        allowed.add('access_log')
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise ValueError(
            '{} contains unknown fields: {}'.format(
                name, ', '.join(unknown)))
    host = value.get('host', '127.0.0.1')
    port = value.get('port', default_port)
    if not isinstance(host, str) or not host:
        raise ValueError('{}.host must be non-empty'.format(name))
    if (isinstance(port, bool) or not isinstance(port, int) or
            not 1 <= port <= 65535):
        raise ValueError('{}.port must be between 1 and 65535'.format(name))
    result = {'host': host, 'port': port}
    if access_log:
        enabled = value.get('access_log', False)
        if not isinstance(enabled, bool):
            raise ValueError('{}.access_log must be a boolean'.format(name))
        result['access_log'] = enabled
    return result


def _resolve(base, value):
    return value if os.path.isabs(value) else os.path.join(base, value)


def deployment_profile(raw):
    deprecated = [name for name in DEPRECATED_CONFIG_FIELDS if name in raw]
    if deprecated:
        raise ValueError(
            'deprecated OCS configuration fields {} are not supported; '
            'set deployment_profile to {} or {} and follow '
            'docs/ocs-agent-architecture.md'.format(
                ', '.join(sorted(deprecated)),
                PYTHON_MONOLITH_HTTP_DIRECT,
                GO_SPLIT_GRPC))
    value = raw.get('deployment_profile')
    if value not in SUPPORTED_DEPLOYMENT_PROFILES:
        raise ValueError(
            'deployment_profile must be {} or {}'.format(
                PYTHON_MONOLITH_HTTP_DIRECT, GO_SPLIT_GRPC))
    return value


def _positive_number(raw, name, default):
    value = raw.get(name, default)
    if (isinstance(value, bool) or
            not isinstance(value, (int, float)) or value <= 0):
        raise ValueError('{} must be positive'.format(name))
    return float(value)


def load_agent_config(path):
    with open(path, 'r') as file_obj:
        raw = json.load(file_obj)
    if not isinstance(raw, dict):
        raise ValueError('configuration root must be an object')

    profile = deployment_profile(raw)
    allowed = set(COMMON_CONFIG_FIELDS)
    if profile == PYTHON_MONOLITH_HTTP_DIRECT:
        allowed.add('http_api')
    else:
        allowed.update(('grpc_api', 'worker', 'go_agent'))
    unknown = sorted(set(raw) - allowed)
    if unknown:
        raise ValueError(
            'configuration contains unknown fields: {}'.format(
                ', '.join(unknown)))
    base = os.path.dirname(os.path.abspath(path))
    model_file = raw.get('model_file')
    capability_file = raw.get('capability_profile_file')
    if not isinstance(model_file, str) or not model_file:
        raise ValueError('model_file is required')
    if not isinstance(capability_file, str) or not capability_file:
        raise ValueError('capability_profile_file is required')
    model_path = os.path.abspath(_resolve(base, model_file))
    capability_path = os.path.abspath(_resolve(base, capability_file))
    model = load_model(model_path)
    capability = load_capability_profile(capability_path)
    if model['profile'] != capability['profile']:
        raise ValueError('model and capability profile must match')

    device = raw.get('device', {})
    if not isinstance(device, dict):
        raise ValueError('device must be an object')
    unknown = sorted(set(device) - set(('consistency_mode',)))
    if unknown:
        raise ValueError(
            'device contains unknown fields: {}'.format(
                ', '.join(unknown)))
    consistency_mode = os.environ.get(
        'OCS_CONSISTENCY_MODE',
        device.get('consistency_mode', 'CACHED_ACK'))
    if consistency_mode not in (
            'CACHED_ACK', 'CACHED_SYNC', 'STRICT_DEVICE'):
        raise ValueError(
            'device.consistency_mode must be CACHED_ACK, CACHED_SYNC, '
            'or STRICT_DEVICE')

    startup_policy = raw.get('startup_policy', 'REQUIRE_MATCH')
    if startup_policy not in ('REQUIRE_MATCH', 'REAPPLY_DESIRED'):
        raise ValueError(
            'startup_policy must be REQUIRE_MATCH or REAPPLY_DESIRED')

    backend = raw.get('backend', {'type': 'p4app'})
    if not isinstance(backend, dict):
        raise ValueError('backend must be an object')
    backend_type = backend.get('type', 'p4app')
    if backend_type not in ('bfrt', 'p4app'):
        raise ValueError('backend.type must be bfrt or p4app')
    backend = dict(backend)
    backend['type'] = backend_type

    control = raw.get('control', {})
    if not isinstance(control, dict):
        raise ValueError('control must be an object')
    unknown = sorted(set(control) - set((
        'lease_seconds', 'reconcile_interval_seconds')))
    if unknown:
        raise ValueError(
            'control contains unknown fields: {}'.format(
                ', '.join(unknown)))
    lease = _positive_number(control, 'lease_seconds', 30)
    reconcile = _positive_number(
        control, 'reconcile_interval_seconds', 30)

    result = {
        'path': os.path.abspath(path),
        'deployment_profile': profile,
        'model_file': model_path,
        'capability_profile_file': capability_path,
        'model': model,
        'capability_profile': capability,
        'device': {'consistency_mode': consistency_mode},
        'startup_policy': startup_policy,
        'backend': backend,
        'control': {
            'lease_seconds': lease,
            'reconcile_interval_seconds': reconcile,
        },
    }

    if profile == PYTHON_MONOLITH_HTTP_DIRECT:
        result['http_api'] = _listener(
            raw, 'http_api', 5000, access_log=True)
        if backend_type == 'bfrt' and result['http_api']['port'] == 5000:
            raise ValueError(
                'Tofino HTTP Agent must not use port 5000 while the '
                'BF-SDE control process owns it')
    else:
        result['grpc_api'] = _listener(raw, 'grpc_api', 9339)
        worker = raw.get('worker', {})
        if not isinstance(worker, dict):
            raise ValueError('worker must be an object')
        unknown = sorted(set(worker) - set((
            'target', 'timeout_seconds')))
        if unknown:
            raise ValueError(
                'worker contains unknown fields: {}'.format(
                    ', '.join(unknown)))
        target = worker.get('target', 'unix:///tmp/ocs-device-worker.sock')
        if not isinstance(target, str) or not target:
            raise ValueError('worker.target is required')
        timeout = _positive_number(worker, 'timeout_seconds', 10)
        go_agent = raw.get('go_agent', {})
        if not isinstance(go_agent, dict):
            raise ValueError('go_agent must be an object')
        unknown = sorted(set(go_agent) - set(('binary',)))
        if unknown:
            raise ValueError(
                'go_agent contains unknown fields: {}'.format(
                    ', '.join(unknown)))
        binary = go_agent.get('binary', '/usr/local/bin/ocs-go-agent')
        if not isinstance(binary, str) or not binary:
            raise ValueError('go_agent.binary must be non-empty')
        result['worker'] = {'target': target, 'timeout_seconds': timeout}
        result['go_agent'] = {'binary': binary}

    return result
