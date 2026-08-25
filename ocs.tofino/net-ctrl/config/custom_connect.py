import json
import os
import re


_MAC_RE = re.compile(r'^[0-9a-fA-F]{2}(?::[0-9a-fA-F]{2}){5}$')
_PORT_RE = re.compile(r'^[0-9]+/[0-9]+$')


def load_config(config_file):
    """Load and validate an OCS hardware profile."""
    if not os.path.exists(config_file):
        raise ValueError("Configuration file not found: {}".format(config_file))

    with open(config_file, 'r') as file_obj:
        config = json.load(file_obj)

    return validate_config(config)


def validate_config(config):
    if not isinstance(config, dict):
        raise ValueError("Configuration root must be an object")

    endpoints = config.get('endpoints')
    if not isinstance(endpoints, list) or not endpoints:
        raise ValueError("endpoints must be a non-empty list")

    normalized = []
    slots = []
    names = []
    front_panel_ports = []
    dev_ports = []
    ipv4_addresses = []

    for endpoint in endpoints:
        if not isinstance(endpoint, dict):
            raise ValueError("Each endpoint must be an object")

        slot = endpoint.get('slot')
        name = endpoint.get('name')
        front_panel_port = endpoint.get('front_panel_port')
        dev_port = endpoint.get('dev_port')
        ipv4 = endpoint.get('ipv4')
        mac = endpoint.get('mac')

        if isinstance(slot, bool) or not isinstance(slot, int) or slot < 1:
            raise ValueError("Endpoint slot must be a positive integer")
        if not isinstance(name, str) or not name:
            raise ValueError("Endpoint {} has an invalid name".format(slot))
        if not isinstance(front_panel_port, str) or not _PORT_RE.match(front_panel_port):
            raise ValueError("Endpoint {} has an invalid front_panel_port".format(slot))
        if isinstance(dev_port, bool) or not isinstance(dev_port, int) or dev_port < 0:
            raise ValueError("Endpoint {} has an invalid dev_port".format(slot))
        if not isinstance(ipv4, str) or not ipv4:
            raise ValueError("Endpoint {} has an invalid ipv4 address".format(slot))
        if mac is not None and (not isinstance(mac, str) or not _MAC_RE.match(mac)):
            raise ValueError("Endpoint {} has an invalid MAC address".format(slot))

        normalized.append({
            'slot': slot,
            'name': name,
            'front_panel_port': front_panel_port,
            'dev_port': dev_port,
            'ipv4': ipv4,
            'mac': mac.lower() if mac else None,
        })
        slots.append(slot)
        names.append(name)
        front_panel_ports.append(front_panel_port)
        dev_ports.append(dev_port)
        ipv4_addresses.append(ipv4)

    normalized.sort(key=lambda item: item['slot'])
    expected_slots = list(range(1, len(normalized) + 1))
    if sorted(slots) != expected_slots:
        raise ValueError("Endpoint slots must be exactly 1..{}".format(len(normalized)))

    for label, values in (
            ('endpoint names', names),
            ('front-panel ports', front_panel_ports),
            ('dev_ports', dev_ports),
            ('IPv4 addresses', ipv4_addresses)):
        if len(values) != len(set(values)):
            raise ValueError("{} must be unique".format(label))

    configured_num_hosts = config.get('num_hosts', len(normalized))
    if configured_num_hosts != len(normalized):
        raise ValueError("num_hosts does not match the endpoint count")

    initial_mapping = config.get('initial_mapping')
    validate_mapping(initial_mapping, len(normalized))

    rest_api = config.get('rest_api', {})
    if not isinstance(rest_api, dict):
        raise ValueError("rest_api must be an object")
    rest_host = rest_api.get('host', '127.0.0.1')
    rest_port = rest_api.get('port', 5000)
    if not isinstance(rest_host, str) or not rest_host:
        raise ValueError("rest_api.host must be a non-empty string")
    if isinstance(rest_port, bool) or not isinstance(rest_port, int) or not 1 <= rest_port <= 65535:
        raise ValueError("rest_api.port must be between 1 and 65535")

    result = dict(config)
    p4_program = config.get('p4_program', 'ocs')
    if not isinstance(p4_program, str) or not re.match(r'^[A-Za-z_][A-Za-z0-9_]*$', p4_program):
        raise ValueError("p4_program must be a valid BFRT identifier")
    result['p4_program'] = p4_program
    result['num_hosts'] = len(normalized)
    result['endpoints'] = normalized
    result['initial_mapping'] = list(initial_mapping)
    result['enable_rest_api'] = bool(config.get('enable_rest_api', True))
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


def endpoint_by_slot(endpoints, slot):
    if isinstance(slot, bool) or not isinstance(slot, int):
        raise ValueError("slot must be an integer")
    try:
        endpoint = endpoints[slot - 1]
    except (IndexError, TypeError):
        raise ValueError("Unknown endpoint slot {}".format(slot))
    if endpoint['slot'] != slot:
        raise ValueError("Endpoint list is not ordered by slot")
    return endpoint


def get_switch_port(endpoints, slot):
    return endpoint_by_slot(endpoints, slot)['dev_port']


# Site-neutral Tofino-model helpers retained for the legacy net-util scripts.
def hostIP(host_id, mask=False, mode='l3'):
    if mode == 'l3':
        address = "10.0.{}.10".format(host_id)
    elif mode == 'l2':
        address = "10.0.10.{}".format(host_id)
    else:
        raise ValueError("mode must be l2 or l3")
    return address + "/24" if mask else address


def hostMAC(host_id):
    return '00:00:00:00:00:{:02x}'.format(host_id)


def switchMAC(host_id):
    return '00:aa:bb:00:00:{:02x}'.format(host_id)


def get_host_interface(host_id):
    if isinstance(host_id, bool) or not isinstance(host_id, int) or not 1 <= host_id <= 32:
        raise ValueError("host_id must be between 1 and 32")
    return "veth{}".format(host_id * 2 - 1)
