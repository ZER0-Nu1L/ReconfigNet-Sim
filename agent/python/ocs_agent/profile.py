import json
import os

from ocs_agent.errors import ValidationError
from ocs_agent.model import Connection, ConnectionSet, PortInventory


CAPABILITY_STATUSES = frozenset((
    'SUPPORTED', 'DERIVED', 'PLANNED', 'UNSUPPORTED', 'OUT_OF_SCOPE'))


def _load_yaml(path):
    try:
        import yaml
    except ImportError:
        raise ValidationError(
            'PyYAML is required to load {}'.format(path))
    if not os.path.exists(path):
        raise ValidationError('Model file not found: {}'.format(path))
    with open(path, 'r') as file_obj:
        data = yaml.safe_load(file_obj)
    if not isinstance(data, dict):
        raise ValidationError('YAML root must be an object')
    return data


def load_model(path):
    data = _load_yaml(path)
    components_root = data.get('openconfig-platform:components', {})
    components = components_root.get('component')
    if not isinstance(components, list):
        raise ValidationError(
            'openconfig-platform:components.component must be a list')
    ports = []
    for component in components:
        if not isinstance(component, dict):
            raise ValidationError('Each component must be an object')
        unknown = set(component) - set(('name', 'index'))
        if unknown:
            raise ValidationError(
                'Unsupported component fields: {}'.format(
                    ', '.join(sorted(unknown))))
        ports.append({
            'name': component.get('name'),
            'index': component.get('index'),
        })
    inventory = PortInventory(ports)

    connections_root = data.get(
        'oc-optical-switch-connections:optical-switch-connections', {})
    unknown = set(connections_root) - set(('port-connection',))
    if unknown:
        raise ValidationError(
            'Unsupported connection model fields: {}'.format(
                ', '.join(sorted(unknown))))
    raw_connections = connections_root.get('port-connection', [])
    if not isinstance(raw_connections, list):
        raise ValidationError('port-connection must be a list')
    connections = []
    for item in raw_connections:
        if not isinstance(item, dict):
            raise ValidationError('Each port-connection must be an object')
        unknown = set(item) - set((
            'connection-name', 'config', 'bidirectional',
            'near-port-name', 'far-port-name'))
        if unknown:
            raise ValidationError(
                'Unsupported port-connection fields: {}'.format(
                    ', '.join(sorted(unknown))))
        config = item.get('config', item)
        if not isinstance(config, dict):
            raise ValidationError('Connection config must be an object')
        unknown = set(config) - set((
            'connection-name', 'bidirectional',
            'near-port-name', 'far-port-name'))
        if unknown:
            raise ValidationError(
                'Unsupported connection config fields: {}'.format(
                    ', '.join(sorted(unknown))))
        name = item.get('connection-name', config.get('connection-name'))
        connections.append(Connection(
            name,
            config.get('near-port-name'),
            config.get('far-port-name'),
            config.get('bidirectional', True)))

    profile_name = data.get('profile', 'p4app-v1')
    if not isinstance(profile_name, str) or not profile_name:
        raise ValidationError('profile must be a non-empty string')
    capability_profile = data.get('capability-profile')
    if (capability_profile is not None and
            (not isinstance(capability_profile, str) or
             not capability_profile)):
        raise ValidationError(
            'capability-profile must be a non-empty string')
    return {
        'profile': profile_name,
        'capability_profile': capability_profile,
        'inventory': inventory,
        'connections': ConnectionSet(inventory, connections),
        'raw': data,
    }


def load_capability_profile(path):
    data = _load_yaml(path)
    profile_name = data.get('profile')
    if not isinstance(profile_name, str) or not profile_name:
        raise ValidationError('Capability profile must define profile')
    model_version = data.get('model-version')
    if not isinstance(model_version, str) or not model_version:
        raise ValidationError(
            'Capability profile must define model-version as a string')

    capabilities = data.get('capabilities')
    if not isinstance(capabilities, list):
        raise ValidationError('capabilities must be a list')
    capability_ids = set()
    for capability in capabilities:
        if not isinstance(capability, dict):
            raise ValidationError('Each capability must be an object')
        capability_id = capability.get('id')
        if not isinstance(capability_id, str) or not capability_id:
            raise ValidationError(
                'Each capability must define a non-empty id')
        if capability_id in capability_ids:
            raise ValidationError(
                'Duplicate capability id {}'.format(capability_id))
        capability_ids.add(capability_id)
        status = capability.get('status')
        if status not in CAPABILITY_STATUSES:
            raise ValidationError(
                'Capability {} has invalid status {}'.format(
                    capability_id, status))

    gnmi = data.get('gnmi')
    if not isinstance(gnmi, dict):
        raise ValidationError('Capability profile must define gnmi')
    version = gnmi.get('version')
    if not isinstance(version, str) or not version:
        raise ValidationError('gnmi.version must be a non-empty string')
    encodings = gnmi.get('encodings')
    if (not isinstance(encodings, list) or not encodings or
            any(not isinstance(item, str) or not item for item in encodings)):
        raise ValidationError('gnmi.encodings must be a non-empty string list')
    models = gnmi.get('models')
    if not isinstance(models, list) or not models:
        raise ValidationError('gnmi.models must be a non-empty list')
    model_names = set()
    for model in models:
        if not isinstance(model, dict):
            raise ValidationError('Each gNMI model must be an object')
        for field in ('name', 'organization', 'version'):
            if not isinstance(model.get(field), str) or not model.get(field):
                raise ValidationError(
                    'Each gNMI model must define {}'.format(field))
        if model['name'] in model_names:
            raise ValidationError(
                'Duplicate gNMI model {}'.format(model['name']))
        model_names.add(model['name'])
    return data


def json_ietf_bytes(value):
    return json.dumps(
        value, separators=(',', ':'), sort_keys=True).encode('utf-8')
