#!/usr/bin/python
import os
import sys
import random
import argparse
from custom_connect import hostIP, hostMAC
from scapy.all import IP, TCP, Ether, sendp, get_if_hwaddr, get_if_list

def get_interface_name(id):
    iface_name = "veth"+ str(int( id*2-1 )) # TODO: Tofino virtual port mapping
    if iface_name in get_if_list():
        return iface_name
    else:
        print("Cannot find host {i} interface" % (id))
        exit(1)

def main():
    if (os.getuid() != 0) :
        print ("ERROR: This script requires root privileges.\n Use 'sudo' to run it.")
        quit()
    
    # TODO: hard code
    src_host_id = 1
    dst_host_id = 2

    print("sending TCP packet to ", get_interface_name(src_host_id))
    pkt = Ether(src = hostMAC(src_host_id), dst = hostMAC(dst_host_id))
    pkt = pkt / IP(src = hostIP(src_host_id), dst = hostIP(dst_host_id))

    # hexdump(pkt)
    # print "len(pkt) = ", len(pkt)
    pkt.show()
    sendp(pkt, iface = get_interface_name(src_host_id), verbose = False)


if __name__ == '__main__':
    main()