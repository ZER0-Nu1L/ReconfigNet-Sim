import json
import os

from ocs_agent.config import load_agent_config


MAX_HOSTS = 8


def load_config(config_file):
    if not os.path.exists(config_file):
        raise ValueError(
            'Configuration file not found: {}'.format(config_file))
    with open(config_file, 'r') as file_obj:
        raw = json.load(file_obj)
    if not isinstance(raw, dict):
        raise ValueError('Configuration root must be an object')

    result = load_agent_config(config_file)
    if result['backend']['type'] != 'p4app':
        raise ValueError('P4App configuration requires backend.type p4app')

    mode = raw.get('mode', 'l3')
    if mode not in ('l2', 'l3'):
        raise ValueError('mode must be either l2 or l3')
    enable_debugger = raw.get('enable_debugger', False)
    if not isinstance(enable_debugger, bool):
        raise ValueError('enable_debugger must be a boolean')

    inventory = result['model']['inventory']
    num_hosts = len(inventory)
    if num_hosts < 2 or num_hosts > MAX_HOSTS or num_hosts % 2 != 0:
        raise ValueError(
            'P4App model must contain an even number of ports between 2 '
            'and {}'.format(MAX_HOSTS))

    result.update({
        'mode': mode,
        'enable_debugger': enable_debugger,
        'num_hosts': num_hosts,
        'initial_connections': result['model']['connections'],
        'initial_mapping': result['model']['connections'].to_permutation(),
        'profile': result['model']['profile'],
    })
    return result
