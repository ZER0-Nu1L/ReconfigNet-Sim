# -*- coding: utf-8 -*-
#!/usr/bin/python

from mininet.net import Mininet
from mininet.topo import Topo
from mininet.log import setLogLevel, info
from mininet.cli import CLI
from mininet.link import TCLink

from p4_mininet import P4Switch, P4Host

import subprocess
import argparse
from time import sleep
import os

_THRIFT_BASE_PORT = 9090

parser = argparse.ArgumentParser(description='P4 Data Center Network Controller')
parser.add_argument('--behavioral-exe', help='Path to behavioral executable',
                    type=str, required=True)
parser.add_argument('--json', help='Path to JSON config file',
                    type=str, required=True)
parser.add_argument('--cli', help='Path to BM CLI',
                    type=str, required=True)
parser.add_argument('--log-file', help='Path to write the switch log file',
                    type=str, action="store", required=False)
parser.add_argument('--num-hosts', help='Number of hosts in the topology (default: 8)',
                    type=int, default=8)
args = parser.parse_args()

class CustomTopo(Topo):
    def __init__(self, num_hosts, log_file, **opts):
        Topo.__init__(self, **opts)
        switch = self.addSwitch('s1',
                                sw_path=args.behavioral_exe,
                                json_path=args.json,
                                log_console = True,
                                log_file = log_file,
                                thrift_port=_THRIFT_BASE_PORT,
                                pcap_dump=True,
                                device_id=0)
        for i in xrange(1, num_hosts+1):
            host_name = "h%d" % i
            ip = "10.0.%d.10/24" % (i)
            mac = "00:04:00:00:00:%02x" % (i)
            self.addHost(host_name, ip=ip, mac=mac)
            self.addLink(host_name, switch, port2=i)

def run_cli_commands(cmds):
    """
    Use the BM CLI tool to issue commands
    : param cmds: String, multiple lines, each line is a CLI command
    """
    cli_cmd = [args.cli, "--json", args.json, "--thrift-port", str(_THRIFT_BASE_PORT)]
    info("Running CLI command: " + " ".join(cli_cmd) + "\n")
    # In Python2, use Popen.communicate () to pass in the command string as standard input
    proc = subprocess.Popen(cli_cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    out, err = proc.communicate(cmds)
    print out
    if err:
        print err

def add_ocs_mapping(net, src_ip, dst_ip):
    cmd = "table_add ocs_mapping NoAction {} {} =>\n".format(src_ip, dst_ip)
    cli_cmd = [args.cli, "--json", args.json, "--thrift-port", str(_THRIFT_BASE_PORT)]
    print "Executing:", cmd.strip()
    proc = subprocess.Popen(cli_cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    out, err = proc.communicate(cmd)
    print out
    if err:
        print err

def del_ocs_mapping(net, src_ip, dst_ip):
    cmd = "table_del ocs_mapping {} {}\n".format(src_ip, dst_ip)
    cli_cmd = [args.cli, "--json", args.json, "--thrift-port", str(_THRIFT_BASE_PORT)]
    print "Executing:", cmd.strip()
    proc = subprocess.Popen(cli_cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    out, err = proc.communicate(cmd)
    print out
    if err:
        print err

def main():
    setLogLevel('info')
    info("***** Creating topology\n")
    topo = CustomTopo(args.num_hosts, args.log_file)
    net = Mininet(topo=topo, host=P4Host, switch=P4Switch, controller=None)
    net.start()

    '''
    Hosts states setup
    '''
    tbl = [(intf.ip, intf.mac) for h in net.hosts for intf in h.intfs.values()]
    for h in net.hosts:
        for intf in h.intfs.values():
            for ip, mac in tbl:
                h.cmd('arp -i %s -s %s %s' % (intf.name, ip, mac))

    num_hosts = args.num_hosts
    sw_mac = ["00:aa:bb:00:00:%02x" % n for n in xrange(0, num_hosts+1)]
    sw_addr = ["10.0.%d.1" % n for n in xrange(0, num_hosts+1)]
    for i in xrange(1, num_hosts+1):
        h = net.get('h%d' % (i))
        h.setARP(sw_addr[i], sw_mac[i])
        h.setDefaultRoute("dev %s via %s" % (h.defaultIntf().name, sw_addr[i]))

    for i in xrange(1, num_hosts+1):
        h = net.get('h%d' % (i))
        h.describe(sw_addr[i], sw_mac[i])

    '''
    Switch states (table entries) setup
    '''
    info("***** Installing default table entries on switch s1\n")
    commands = []
    commands.append("table_set_default ipv4_lpm ingress._drop")
    commands.append("table_set_default forward ingress._drop")
    commands.append("table_set_default ocs_mapping egress._drop")
    commands.append("table_set_default send_frame egress._drop")

    # for i in xrange(1, args.num_hosts+1):
    #     port = i
    #     ip = "10.0.%d.10" % (i-1)
    #     commands.append("table_add arp_forward set_egress_port {} => {}".format(ip, port))
        
    
    for i in xrange(1, args.num_hosts+1):
        port = i
        ip = "10.0.%d.10" % (i)
        host_mac = "00:04:00:00:00:%02x" % (i)
        route_mac = "00:aa:bb:00:00:%02x" % (i)

        commands.append("table_add ipv4_lpm ingress.set_nhop {}/32 => {} {}".format(ip, ip, port))
        commands.append("table_add send_frame egress.rewrite_mac {} => {}".format(port, route_mac))
        commands.append("table_set_default egress.ocs_mapping NoAction")
        commands.append("table_add forward ingress.set_dmac {} => {}".format(ip, host_mac))

    if args.num_hosts >= 2:
        for i in xrange(1, args.num_hosts, 2):
            commands.append("table_add ocs_mapping NoAction {} {} =>".format(i, i+1))
            commands.append("table_add ocs_mapping NoAction {} {} =>".format(i+1, i))

    # Combine all commands into one string, with each command ending in a newline.
    cmds_str = "\n".join(commands) + "\n"
    info("***** Generated CLI commands:\n" + cmds_str + "\n")
    run_cli_commands(cmds_str)

    info("***** All table entries installed. Network is ready!\n")
    info("***** Use add_ocs_mapping(net, src_ip, dst_ip) and del_ocs_mapping(net, src_ip, dst_ip) to modify ocs_mapping dynamically.\n")

    container = os.environ['HOSTNAME']

    log_file = args.log_file
    print('CLI from host operating system using this command:')
    print('  docker exec -t -i %s simple_switch_CLI' % container)
    print('To view the switch log, run this command from your host OS:')
    print('  docker exec -t -i %s tail -f %s' % (container, log_file))
    print('To run the switch debugger, run this command from your host OS:')
    print('  docker exec -t -i %s bm_p4dbg' % container)
    CLI(net)
    net.stop()

if __name__ == '__main__':
    main()
