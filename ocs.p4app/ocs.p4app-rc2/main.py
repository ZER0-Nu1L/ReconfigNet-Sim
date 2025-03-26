#!/usr/bin/env python3
from p4app import P4Mininet
from mininet.log import setLogLevel, info
from mininet.cli import CLI

from p4util.custom_topo import CustomTopo, setup_host_entries, switch_name
from p4util.sw_fwd_p4runtime import setup_switch_basic_entries
from api.northbound import run_rest_api
import threading
import json
import os


def main():
    setLogLevel('info')
    '''
    Load configuration from TOML file
    '''
    config_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'config/p4app.json')
    if not os.path.exists(config_file):
        info("Warning: Configuration file not found. Using default configuration.")
        config = {}
    else:
        with open(config_file, 'r') as file:
            config = json.load(file)

    mode = config.get('mode', 'l3')
    num_hosts = config.get('num_hosts', 8)
    enable_debugger = config.get('enable_debugger', False)

    '''
    Topo set up & Initialize the switch and host
    '''
    topo = CustomTopo(num_hosts, mode)
    net = P4Mininet(program='ocs.p4', topo=topo, enable_debugger=enable_debugger)
    # NOTE: P4Mininet set setup every host ARP to every host by default.
    net.start()
    # Hosts & Switch states setup
    setup_host_entries(net, num_hosts, mode)
    setup_switch_basic_entries(net, num_hosts)

    '''
    Start the Reconfigurable Network Northbound Interface (REST API) thread'
    '''
    default_pi = [i + 1 if i % 2 == 1 else i - 1 for i in range(1, num_hosts + 1)]
    default_pi_state = [1]
    if config.get('enable_rest_api', True):
        rest_api_config = config.get('rest_api', {})
        rest_api_host = rest_api_config.get('host', '127.0.0.1')
        rest_api_port = rest_api_config.get('port', 5000)
        api_thread = threading.Thread(
            target = run_rest_api,
            args = (default_pi, default_pi_state, net, num_hosts,
                    rest_api_host, rest_api_port),
            daemon=True
        )
        api_thread.start()

    '''
    Debug in container
    '''
    if enable_debugger:
        container = os.environ['HOSTNAME']
        s1_logfile = "/tmp/p4app-logs/p4s.{}.log".format(switch_name) # NOTE: hard code from p4app
        print('---------------------------------------------------------')
        print('CLI from host operating system using this command:')
        print('  docker exec -t -i %s simple_switch_CLI\n' % container)
        print('To view the switch %s log, run this command from your host OS:' % switch_name)
        print('  docker exec -t -i %s tail -f %s\n' % (container, s1_logfile))
        print('To run the switch debugger, run this command from your host OS:')
        print('  docker exec -t -i %s bm_p4dbg\n' % container)
        print('---------------------------------------------------------')    

    CLI(net)
    net.stop()

if __name__ == '__main__':
    main()
