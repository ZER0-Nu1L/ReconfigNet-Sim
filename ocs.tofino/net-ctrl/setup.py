from config.custom_connect import load_config
from p4util.sw_fwd_p4runtime import setup_switch_basic_entries, show_switch_basic_entries
from p4util.ocs_map_p4runtime import init_ocs_mapping, update_ocs_mapping
from p4util.cleanup import clear_all

import os

config_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'config/project_conf.json')
config = load_config(config_file)
num_hosts = config.get('num_hosts', 8)

p4_pipe = bfrt.ocs.pipe
clear_all(p4_pipe)


setup_switch_basic_entries(p4_pipe, num_hosts)
show_switch_basic_entries(p4_pipe)

default_pi_state = [1]
default_pi = [i + 1 if i % 2 == 1 else i - 1 for i in range(1, num_hosts + 1)]
init_ocs_mapping(p4_pipe, default_pi_state, num_hosts)

new_pi = [3,4,1,2,7,8,5,6]
update_ocs_mapping(p4_pipe, new_pi, default_pi, default_pi_state, num_hosts)


bfrt.complete_operations()