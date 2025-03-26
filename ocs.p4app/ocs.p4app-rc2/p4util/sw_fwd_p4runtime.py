# switch_forwarding_p4runtime
from p4util.custom_topo import hostIP, hostMAC, switchMAC
from mininet.log import info

def setup_switch_basic_entries(net, num_hosts):
    tb_forward_entries = []
    tb_ipv4_lpm_entries = []
    # tb_arp_forward_entries = []
    tb_send_frame_entries = []
    all_table_entries = [
        tb_ipv4_lpm_entries,
        # tb_arp_forward_entries,
        tb_forward_entries,
        tb_send_frame_entries,
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


    s1 = net.get('s1')
    for table_entries in all_table_entries:
        for table_entry in table_entries:
            s1.insertTableEntry(table_entry)

    info("***** Installing default table entries on switch s1 *****\n")
    s1.printTableEntries()
    info("***** All table entries installed. Network is ready! *****\n")