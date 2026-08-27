import os
import sys


configured_file = os.environ.get('OCS_CONFIG_FILE')
script_dir = os.environ.get('OCS_NET_CTRL_DIR')
if not script_dir and configured_file:
    script_dir = os.path.dirname(os.path.dirname(os.path.abspath(configured_file)))
if not script_dir:
    script_dir = os.path.dirname(os.path.abspath(globals().get('__file__', os.getcwd())))
if script_dir not in sys.path:
    sys.path.insert(0, script_dir)

from config.device_profile import load_config
from bfrt.cleanup import clear_all
from bfrt.l3_tables import setup_switch_basic_entries, show_switch_basic_entries
from bfrt.ocs_mapping import init_ocs_mapping


def write_initialization_marker(marker_path):
    """Write an optional marker after all BFRT writes have completed.

    The model CI uses this marker to distinguish a successful execution of
    this script from a bfshell process which merely connected and exited.
    Normal deployments do not set the environment variable, so no marker is
    written outside of an explicit verification run.
    """
    if not marker_path:
        return

    marker_path = os.path.abspath(marker_path)
    marker_dir = os.path.dirname(marker_path)
    os.makedirs(marker_dir, exist_ok=True)
    temporary_path = marker_path + '.tmp'
    try:
        with open(temporary_path, 'w', encoding='utf-8') as marker:
            marker.write('ocs-bfrt-initialized\n')
            marker.flush()
            os.fsync(marker.fileno())
        os.replace(temporary_path, marker_path)
    finally:
        if os.path.exists(temporary_path):
            os.unlink(temporary_path)


config_file = os.environ.get('OCS_CONFIG_FILE')
if not config_file:
    raise RuntimeError(
        "OCS_CONFIG_FILE is required; pass an explicit deployment profile. "
        "See config/device-profile.example.json for the schema.")
config = load_config(config_file)
endpoints = config['endpoints']

print("Loading OCS profile {} from {}".format(
    config.get('fabric', 'unnamed'), config_file))
print("Endpoint dev_ports: {}".format([
    endpoint['dev_port'] for endpoint in endpoints]))

p4_program = config.get('p4_program', 'ocs')
p4_pipe = getattr(bfrt, p4_program).pipe
clear_all(p4_pipe)
installed_l3_entries = setup_switch_basic_entries(p4_pipe, endpoints)
show_switch_basic_entries(p4_pipe)

current_mapping = list(config['initial_mapping'])
runtime_state = {
    'status': 1,
    'mode': 'ocs',
    'revision': 0,
}
init_ocs_mapping(p4_pipe, current_mapping, runtime_state, endpoints)
bfrt.complete_operations()

print("Installed {} IPv4/MAC endpoint pairs".format(installed_l3_entries))
print("Initial OCS mapping: {}".format(current_mapping))

write_initialization_marker(os.environ.get('OCS_BFRT_INIT_MARKER'))
if os.environ.get('OCS_BFRT_INIT_MARKER'):
    print("BFRT initialization marker written")
