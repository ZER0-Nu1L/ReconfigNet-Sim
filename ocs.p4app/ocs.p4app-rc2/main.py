#!/usr/bin/env python3
from p4app import P4Mininet
from mininet.topo import Topo
from mininet.log import setLogLevel, info
from mininet.cli import CLI
import struct, os
import json

def hostIP(i, mask=False):
    '''
    l3 mode
    '''
    if mask == True:
        return "10.0.%d.10/24" % (i)
    else:
        return "10.0.%d.10" % (i)
    # l2 mode
    # if mask == True:
    #     return "10.0.10.%d/24" % (i)
    # else:
    #     return "10.0.10.%d" % (i)

def hostMAC(i):
    return '00:00:00:00:00:%02x' % (i)

def switchIP(i):
    return "10.0.%d.1" % (i)

def switchMAC(i):
    return '00:aa:bb:00:00:%02x' % (i)

class CustomTopo(Topo):
    def __init__(self, num_hosts, mode, **opts):
        Topo.__init__(self, **opts)
        switch = self.addSwitch('s1')

        for i in range(1, num_hosts+1):
            host = self.addHost('h%d' % i, ip = hostIP(i, mask=True), mac = hostMAC(i))
            self.addLink(host, switch, port2=i) # port2: dest port
            # NOTE: Only responsible for L2 connectivity
            # gateway, etc. does not seem to be set by default

def main():
    setLogLevel('info')
    # Load configuration from TOML file
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

    topo = CustomTopo(num_hosts, mode)
    net = P4Mininet(program='ocs.p4', topo=topo, enable_debugger=enable_debugger)
    # NOTE: set setup every host ARP to every host by default.
    net.start()

    '''
    Hosts states setup
    '''
    for i in range(1, num_hosts+1):
        h = net.get('h%d' % (i))
        if mode == "l2":
            h.setDefaultRoute("dev %s" % h.defaultIntf().name)
        elif mode == "l3":
            h.setARP(switchIP(i), switchMAC(i))
            h.setDefaultRoute("dev %s via %s" % (h.defaultIntf().name, switchIP(i)))
        else:
            assert mode != 'l2' and mode != 'l3'
            exit()

    '''
    Switch states (table entries) setup
    '''
    tb_forward_entries = []
    tb_ipv4_lpm_entries = []
    # tb_arp_forward_entries = []
    tb_send_frame_entries = []
    tb_ocs_mapping_entries = [] # TODO: 
    all_table_entries = [
        tb_ipv4_lpm_entries, 
        # tb_arp_forward_entries, 
        tb_forward_entries,
        tb_send_frame_entries,
        tb_ocs_mapping_entries # TODO: 
    ]
    for i in range(1, num_hosts+1):
        port = i
        
        tb_ipv4_lpm_entries.append(dict(
                table_name      = 'ingress.ipv4_lpm',
                match_fields    = {'hdr.ipv4.dstAddr': [hostIP(i), 32]}, 
                action_name     = 'ingress.set_nhop',
                action_params   = {'nhop_ipv4': hostIP(i), 'port': port}
                ))
                # NOTE: L3 forward table

        # tb_arp_forward_entries.append(dict(
        #         table_name      = 'ingress.arp_forward',
        #         match_fields    = {'hdr.arp.tpa': [hostIP(i)]},
        #         action_name     = 'ingress.set_egress_port',
        #         action_params   = {'port': port}
        #         ))

        tb_forward_entries.append(dict(
                table_name      = 'ingress.forward',
                match_fields    = {'meta.ingress_metadata.nhop_ipv4': [hostIP(i)]},
                action_name     = 'ingress.set_dmac',
                action_params   = {'dmac': hostMAC(i)}
                ))
                # NOTE: This is the ARP table of switch as an L3 device 
                # bonding the protocol layers of L2 and L3 (L3 -> L2)


        tb_send_frame_entries.append(dict(
                table_name      = 'egress.send_frame',
                match_fields    = {'standard_metadata.egress_port': [port]},
                action_name     = 'egress.rewrite_mac',
                action_params   = {'smac' : switchMAC(i)}
                ))
                # NOTE: L2 Logic


        if num_hosts >= 2:
            for i in range(1, num_hosts, 2):
                tb_ocs_mapping_entries.append(dict(
                        table_name    = 'egress.ocs_mapping',
                        match_fields  = {'standard_metadata.ingress_port': [(i)], 'standard_metadata.egress_port': [(i+1)]},
                        action_name   = 'NoAction',
                        action_params = {}
                        ))
                tb_ocs_mapping_entries.append(dict(
                        table_name    = 'egress.ocs_mapping',
                        match_fields  = {'standard_metadata.ingress_port': [(i+1)], 'standard_metadata.egress_port': [(i)]},
                        action_name   = 'NoAction',
                        action_params = {}))

    s1 = net.get('s1')
    for table_entries in all_table_entries:
        for table_entry in table_entries:
            s1.insertTableEntry(table_entry)
    
    info("***** Installing default table entries on switch s1 *****\n")
    s1.printTableEntries()
    info("***** All table entries installed. Network is ready! *****\n")
    
    # NOTE: for Debug in container
    if(enable_debugger):
        container = os.environ['HOSTNAME']
        s1_logfile = "/tmp/p4app-logs/p4s.{}.log".format(s1.name)
        print('---------------------------------------------------------')
        print('CLI from host operating system using this command:')
        print('  docker exec -t -i %s simple_switch_CLI\n' % container)
        print('To view the switch %s log, run this command from your host OS:' % s1.name)
        print('  docker exec -t -i %s tail -f %s\n' % (container, s1_logfile))
        print('To run the switch debugger, run this command from your host OS:')
        print('  docker exec -t -i %s bm_p4dbg\n' % container)
        print('---------------------------------------------------------')    

    loss = net.pingAll(timeout = 1)
    if(enable_debugger):
        CLI(net)
    else:
        # assert loss == 0
        pass
    
    net.stop()

if __name__ == '__main__':
    main()
