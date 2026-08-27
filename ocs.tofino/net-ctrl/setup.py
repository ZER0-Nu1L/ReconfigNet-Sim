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

from config.custom_connect import load_config
from p4util.cleanup import clear_all
from p4util.sw_fwd_p4runtime import setup_switch_basic_entries, show_switch_basic_entries
from p4util.ocs_map_p4runtime import init_ocs_mapping


config_file = os.environ.get('OCS_CONFIG_FILE')
if not config_file:
    raise RuntimeError(
        "OCS_CONFIG_FILE is required; pass an explicit deployment profile. "
        "See config/project_conf.example.json for the schema.")
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
