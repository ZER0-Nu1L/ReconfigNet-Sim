#!/usr/bin/python
import os, sys
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if project_root not in sys.path:
    sys.path.insert(0, project_root)
from config.custom_connect import get_host_interface, hostMAC, hostIP, load_config

import argparse
from scapy.all import IP, Ether, sendp, get_if_hwaddr


def check_root():
    if os.getuid() != 0:
        print("ERROR: This script requires root privileges. Use 'sudo' to run it.")
        sys.exit(1)

def main():
    check_root()
        
    parser = argparse.ArgumentParser(description="Send a IP packet between hosts")
    parser.add_argument("--src", type=int, required=True, help="Source host ID")
    parser.add_argument("--dst", type=int, required=True, help="Destination host ID")
    args = parser.parse_args()
    
    src_host_id = args.src
    dst_host_id = args.dst

    config_file = os.path.join(project_root, 'config/project_conf.json')
    config = load_config(config_file)
    num_hosts = config.get('num_hosts', 8)

    if src_host_id > num_hosts or dst_host_id > num_hosts:
        print("ERROR: src hostID or dst hostID is bigger than number of hosts!")
        quit()

    pkt = Ether(src=hostMAC(src_host_id), dst=hostMAC(dst_host_id))
    pkt = pkt / IP(src=hostIP(src_host_id), dst=hostIP(dst_host_id))

    # hexdump(pkt)
    # print "len(pkt) = ", len(pkt)
    pkt.show()

    src_iface = get_host_interface(src_host_id)
    dst_iface = get_host_interface(dst_host_id)
    sendp(pkt, iface = src_iface, verbose = False)
    print(f"sending TCP packet from h%s(%s,%s) to h%s(%s,%s)" % (src_host_id, hostIP(src_host_id), src_iface, dst_host_id, hostIP(dst_host_id), dst_iface))

if __name__ == '__main__':
    main()