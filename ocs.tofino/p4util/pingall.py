#!/usr/bin/env python3
import os
import sys
import time
import threading
from concurrent.futures import ThreadPoolExecutor
from scapy.all import ICMP, Ether, IP, srp1, sendp, sniff
from custom_connect import hostIP, hostMAC, switchMAC, get_host_interface, load_config, get_switch_port

# ================== Global Config ==================
TIMEOUT = 1  # Per-probe timeout
MAX_WORKERS = 20  # Concurrent thread pool size
COLOR_ENABLED = True  # ANSI color codes

# ================== Utility Functions ==================
def check_root():
    """Verify script runs with root privileges"""
    if os.getuid() != 0:
        print("ERROR: Requires root privileges. Use 'sudo'.", file=sys.stderr)
        sys.exit(1)

# ================== ICMP Responder Handling ==================
def start_icmp_responder(host_id):
    """Launch ICMP echo responder for specified host"""
    iface = get_host_interface(host_id)
  
    def handle_packet(pkt):
        if IP in pkt and ICMP in pkt and pkt[ICMP].type == 8:  # Echo Request
            reply = Ether(src=pkt[Ether].dst, dst=pkt[Ether].src) \
                  / IP(src=pkt[IP].dst, dst=pkt[IP].src) \
                  / ICMP(type=0, code=0, id=pkt[ICMP].id, seq=pkt[ICMP].seq)
            sendp(reply, iface=iface, verbose=0)
  
    # Start packet processing thread
    sniff_thread = threading.Thread(
        target=sniff,
        kwargs={'iface': iface, 'prn': handle_packet, 'filter': 'icmp', 'store': 0},
        daemon=True
    )
    sniff_thread.start()
    return sniff_thread

# ================== Connectivity Testing ==================
def test_connectivity(src_id, dst_id, mode):
    """Test connectivity between two hosts"""
    if src_id == dst_id:
        return True  # Skip self-test
  
    dst_mac = switchMAC(src_id) if mode == 'l3' else hostMAC(dst_id)
    packet = Ether(src=hostMAC(src_id), dst=dst_mac) \
           / IP(src=hostIP(src_id, mode=mode), dst=hostIP(dst_id, mode=mode)) \
           / ICMP(type=8, code=0)
  
    try:
        response = srp1(
            packet, 
            iface=get_host_interface(src_id),
            timeout=TIMEOUT,
            verbose=0
        )
        return response is not None
    except Exception as e:
        print(f"Error h{src_id}->h{dst_id}: {str(e)}", file=sys.stderr)
        return False

# ================== Result Visualization ==================
def print_mapping_table(connectivity, num_hosts):
    """Display active host-to-host connectivity mappings"""
    print("\n\033[1;36mActive Connectivity Mappings:\033[0m")
    print(f"{'Source':^10} ──► {'Destination':^10}")
    print(f"{'──────':^10}    {'───────────':^10}")
  
    # Track displayed pairs to avoid duplicates
    displayed = set()
  
    for src in range(num_hosts):
        for dst in range(num_hosts):
            if src != dst and connectivity[src][dst]:
                # Display each connection once
                print(f"{src+1:^8} ──► {dst+1:^10}")
                displayed.add((src, dst))

def print_connectivity_matrix(matrix, num_hosts):
    """Display matrix showing all host connections"""
    print("\n\033[1;36mConnection Matrix:\033[0m")
    header = "     " + " ".join([f"\033[1;33m{h:2}\033[0m" for h in range(1, num_hosts+1)])
    print(header)
  
    for row in range(num_hosts):
        cells = [f"\033[1;33m{row+1:2}\033[0m  "] + [
            "\033[1;37m■\033[0m " if row == col else
            "\033[1;32m✓\033[0m " if matrix[row][col] else "\033[1;31m✗\033[0m "
            for col in range(num_hosts)
        ]
        print(" ".join(cells))

# ================== Main Execution Flow ==================
def main():
    check_root()
  
    config = load_config()
    num_hosts = config.get('num_hosts', 8)
    mode = config.get('mode', 'l3')
  
    # Start response services
    print("\033[1;34mInitializing ICMP responders...\033[0m")
    [start_icmp_responder(h) for h in range(1, num_hosts+1)]
    time.sleep(1)  # Allow responder initialization
  
    # Perform connectivity tests
    connectivity = [[False]*num_hosts for _ in range(num_hosts)]
    print("\033[1;34mTesting network connectivity...\033[0m")
  
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = []
        for src in range(1, num_hosts+1):
            for dst in range(1, num_hosts+1):
                if src == dst: continue
                futures.append(executor.submit(test_connectivity, src, dst, mode))
      
        # Process results
        for idx, future in enumerate(futures):
            src = (idx // (num_hosts-1)) + 1
            dst = (idx % (num_hosts-1)) + 1
            dst = dst if dst < src else dst + 1  # Adjust for skipped diagonal
            connectivity[src-1][dst-1] = future.result()
  
    # Display results
    print_mapping_table(connectivity, num_hosts)
    print_connectivity_matrix(connectivity, num_hosts)

if __name__ == "__main__":
    main()