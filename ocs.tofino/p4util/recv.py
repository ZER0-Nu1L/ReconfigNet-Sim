#!/usr/bin/python
import os
import argparse
from scapy.all import IP, sniff
from custom_connect import get_interface_name

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
    if os.getuid() != 0:
        print("ERROR: This script requires root privileges.\n Use 'sudo' to run it.")
        quit()
    
    parser = argparse.ArgumentParser(description="Receive packets on the interface corresponding to the given host id")
    parser.add_argument("--host", type=int, required=True, help="Host id to use for receiving packets")
    args = parser.parse_args()
    
    iface = get_interface_name(args.host)
    print("Sniffing on interface:", iface)
    print("Press Ctrl-C to stop...")
    sniff(iface=iface, prn=lambda pkt: handle_pkt(pkt))

if __name__ == '__main__':
    main()