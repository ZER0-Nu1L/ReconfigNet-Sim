#!/usr/bin/python

import os
import sys
from scapy.all import IP, TCP, UDP, Raw, sniff

def handle_pkt(pkt):
    print("="*50)
    print("Receive packet:")
    pkt.show()


    if IP in pkt:
        ip_layer = pkt[IP]
        print("IP: srcIP %s -> dstIP %s" % (ip_layer.src, ip_layer.dst))
    else:
        print("IP layer not found in packet")

def main():
    if (os.getuid() !=0) :
        print ("ERROR: This script requires root privileges.\n Use 'sudo' to run it.")
        quit()
    try:
        iface=sys.argv[1]
    except:
        iface="veth0"

    print("Sniffing on ", iface)
    print("Press Ctrl-C to stop...")
    sniff(iface=iface, prn = lambda x: handle_pkt(x))

if __name__ == '__main__':
    main()