from config.custom_connect import load_config
from p4util.cleanup import clear_all
from p4util.sw_fwd_p4runtime import setup_switch_basic_entries, show_switch_basic_entries
from p4util.ocs_map_p4runtime import init_ocs_mapping, update_ocs_mapping
from api.northbound import run_rest_api

import threading
import os

# Load configuration from JSON file
config_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'config/project_conf.json')
config = load_config(config_file)
num_hosts = config.get('num_hosts', 8)

# Switch states setup
p4_pipe = bfrt.ocs.pipe
clear_all(p4_pipe)

setup_switch_basic_entries(p4_pipe, num_hosts)
show_switch_basic_entries(p4_pipe)

# Start the Reconfigurable Network Northbound Interface (REST API) thread
default_pi_state = [1]
default_pi = [i + 1 if i % 2 == 1 else i - 1 for i in range(1, num_hosts + 1)]

if config.get('enable_rest_api', True):
    rest_api_config = config.get('rest_api', {})
    rest_api_host = rest_api_config.get('host', '127.0.0.1')
    rest_api_port = rest_api_config.get('port', 5000)
    api_thread = threading.Thread(
        target = run_rest_api,
        args = (default_pi, default_pi_state, p4_pipe, num_hosts,
                rest_api_host, rest_api_port),
        daemon=True
    )
    api_thread.start()

bfrt.complete_operations()