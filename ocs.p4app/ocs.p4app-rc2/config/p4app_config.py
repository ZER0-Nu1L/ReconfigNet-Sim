import json
import os


MAX_HOSTS = 8


def load_config(config_file):
    if not os.path.exists(config_file):
        raise ValueError("Configuration file not found: {}".format(config_file))

    with open(config_file, 'r') as file_obj:
        config = json.load(file_obj)

    return validate_config(config)


def validate_config(config):
    if not isinstance(config, dict):
        raise ValueError("Configuration root must be an object")

    forwarding_mode = config.get('mode', 'l3')
    if forwarding_mode not in ('l2', 'l3'):
        raise ValueError("mode must be either l2 or l3")

    num_hosts = config.get('num_hosts', 8)
    if (isinstance(num_hosts, bool) or not isinstance(num_hosts, int) or
            num_hosts < 2 or num_hosts > MAX_HOSTS or num_hosts % 2 != 0):
        raise ValueError("num_hosts must be an even integer between 2 and {}".format(
            MAX_HOSTS))

    initial_mapping = config.get('initial_mapping')
    validate_mapping(initial_mapping, num_hosts)

    rest_api = config.get('rest_api', {})
    if not isinstance(rest_api, dict):
        raise ValueError("rest_api must be an object")
    rest_host = rest_api.get('host', '127.0.0.1')
    rest_port = rest_api.get('port', 5000)
    if not isinstance(rest_host, str) or not rest_host:
        raise ValueError("rest_api.host must be a non-empty string")
    if (isinstance(rest_port, bool) or not isinstance(rest_port, int) or
            not 1 <= rest_port <= 65535):
        raise ValueError("rest_api.port must be between 1 and 65535")

    for name in ('enable_debugger', 'enable_rest_api'):
        default = False if name == 'enable_debugger' else True
        value = config.get(name, default)
        if not isinstance(value, bool):
            raise ValueError("{} must be a boolean".format(name))

    result = dict(config)
    result['mode'] = forwarding_mode
    result['num_hosts'] = num_hosts
    result['initial_mapping'] = list(initial_mapping)
    result['enable_debugger'] = config.get('enable_debugger', False)
    result['enable_rest_api'] = config.get('enable_rest_api', True)
    result['rest_api'] = {'host': rest_host, 'port': rest_port}
    return result


def validate_mapping(mapping, num_hosts):
    """Validate a fixed-point-free, symmetric permutation of 1..N."""
    if not isinstance(mapping, list) or len(mapping) != num_hosts:
        raise ValueError("new_pi must contain exactly {} slots".format(num_hosts))

    for value in mapping:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError("new_pi entries must be integers")

    expected = list(range(1, num_hosts + 1))
    if sorted(mapping) != expected:
        raise ValueError("new_pi must be a permutation of 1..{}".format(num_hosts))

    for source_slot, destination_slot in enumerate(mapping, start=1):
        if source_slot == destination_slot:
            raise ValueError("new_pi must not contain self mappings")
        if mapping[destination_slot - 1] != source_slot:
            raise ValueError("new_pi must contain symmetric two-way pairs")

    return True
