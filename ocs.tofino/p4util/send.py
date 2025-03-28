#!/usr/bin/python
import os
import argparse
from custom_connect import hostIP, hostMAC, load_config, get_interface_name
from scapy.all import IP, Ether, sendp, get_if_hwaddr


def main():
    if os.getuid() != 0:
        print("ERROR: This script requires root privileges.\n Use 'sudo' to run it.")
        quit()
    
    parser = argparse.ArgumentParser(description="Send a IP packet between hosts")
    parser.add_argument("--src", type=int, required=True, help="Source host ID")
    parser.add_argument("--dst", type=int, required=True, help="Destination host ID")
    args = parser.parse_args()
    
    src_host_id = args.src
    dst_host_id = args.dst
    
    config = load_config()  # 调用 load_config() 函数加载配置
    num_hosts = config.get('num_hosts', 8)

    if src_host_id > num_hosts or dst_host_id > num_hosts:
        print("ERROR: src hostID or dst hostID is bigger than number of hosts!")
        quit()

    pkt = Ether(src=hostMAC(src_host_id), dst=hostMAC(dst_host_id))
    pkt = pkt / IP(src=hostIP(src_host_id), dst=hostIP(dst_host_id))

    # hexdump(pkt)
    # print "len(pkt) = ", len(pkt)
    pkt.show()

    src_iface = get_interface_name(src_host_id)
    dst_iface = get_interface_name(dst_host_id)
    sendp(pkt, iface = src_iface, verbose = False)
    print(f"sending TCP packet from h%s(%s,%s) to h%s(%s,%s)" % (src_host_id, hostIP(src_host_id), src_iface, dst_host_id, hostIP(dst_host_id), dst_iface))

if __name__ == '__main__':
    main()