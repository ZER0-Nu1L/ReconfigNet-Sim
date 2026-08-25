#!/usr/bin/env python3
import os
import sys
import threading

from mininet.cli import CLI
from mininet.log import setLogLevel
from p4app import P4Mininet, P4Program

from api.northbound import run_rest_api
from config.p4app_config import load_config
from p4util.custom_topo import CustomTopo, setup_host_entries, switch_name
from p4util.ocs_map_p4runtime import init_ocs_mapping
from p4util.sw_fwd_p4runtime import setup_switch_basic_entries


def main():
    setLogLevel('info')
    if len(sys.argv) > 1 and sys.argv[1] == 'compile':
        P4Program('ocs.p4').compile()
        return

    project_dir = os.path.dirname(os.path.abspath(__file__))
    config = load_config(os.path.join(project_dir, 'config', 'p4app.json'))

    forwarding_mode = config['mode']
    num_hosts = config['num_hosts']
    enable_debugger = config['enable_debugger']

    topo = CustomTopo(num_hosts, forwarding_mode)
    net = P4Mininet(
        program='ocs.p4', topo=topo, enable_debugger=enable_debugger)
    net.start()

    try:
        setup_host_entries(net, num_hosts, forwarding_mode)
        setup_switch_basic_entries(net, num_hosts)

        switch = net.get(switch_name)
        current_mapping = list(config['initial_mapping'])
        runtime_state = {
            'status': 1,
            'mode': 'ocs',
            'revision': 0,
        }
        init_ocs_mapping(switch, current_mapping, runtime_state, num_hosts)

        if config['enable_rest_api']:
            rest_api = config['rest_api']
            api_thread = threading.Thread(
                target=run_rest_api,
                args=(current_mapping, runtime_state, switch, num_hosts,
                      rest_api['host'], rest_api['port']))
            api_thread.daemon = True
            api_thread.start()

        if enable_debugger:
            container = os.environ['HOSTNAME']
            switch_log = "/tmp/p4app-logs/p4s.{}.log".format(switch_name)
            print('---------------------------------------------------------')
            print('CLI from host operating system using this command:')
            print('  docker exec -t -i %s simple_switch_CLI\n' % container)
            print('To view the switch %s log, run this command from your host OS:' %
                  switch_name)
            print('  docker exec -t -i %s tail -f %s\n' %
                  (container, switch_log))
            print('To run the switch debugger, run this command from your host OS:')
            print('  docker exec -t -i %s bm_p4dbg\n' % container)
            print('---------------------------------------------------------')

        CLI(net)
    finally:
        net.stop()


if __name__ == '__main__':
    main()
