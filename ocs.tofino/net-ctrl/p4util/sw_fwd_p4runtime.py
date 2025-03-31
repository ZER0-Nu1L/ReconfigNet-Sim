import bfrtcli
from bfrtcli import bfrt

from config.custom_connect import hostIP, hostMAC, get_switch_port

def setup_switch_basic_entries(p4_pipe, num_hosts):
    tb_ipv4_lpm =  p4_pipe.SwitchIngress.ipv4_lpm
    tb_forward =   p4_pipe.SwitchIngress.forward
    for host_id in range(1, num_hosts+1):
        tb_ipv4_lpm.add_with_set_nhop(
            dst_addr = hostIP(host_id),
            dst_addr_p_length = 32,
            nhop_ipv4 = hostIP(host_id),
            port = get_switch_port(host_id)
        )
        tb_forward.add_with_set_dmac(
            nhop_ipv4 = hostIP(host_id),
            dmac = hostMAC(host_id)
        )

def show_switch_basic_entries(p4_pipe):
    tb_ipv4_lpm =  p4_pipe.SwitchIngress.ipv4_lpm
    tb_forward =   p4_pipe.SwitchIngress.forward

    print ("Table ipv4_lpm:")
    tb_ipv4_lpm.dump(table=True)
    
    print ("Table tb_forward:")
    tb_forward.dump(table=True)


'''
# case: 

p4_pipe = bfrt.ocs.pipe
tb_ipv4_lpm =  p4_pipe.SwitchIngress.ipv4_lpm
tb_forward =  p4_pipe.SwitchIngress.forward


tb_ipv4_lpm.add_with_set_nhop(
    dst_addr = "10.0.1.10",
    dst_addr_p_length = 32,
    nhop_ipv4 = "10.0.1.10",
    port = 0
)
tb_ipv4_lpm.add_with_set_nhop(
    dst_addr = "10.0.2.10",
    dst_addr_p_length = 32,
    nhop_ipv4 = "10.0.2.10",
    port = 1
)
tb_forward.add_with_set_dmac(
    nhop_ipv4="10.0.1.10",
    dmac='00:00:00:00:00:01'
)
tb_forward.add_with_set_dmac(
    nhop_ipv4="10.0.2.10",
    dmac='00:00:00:00:00:02'
)
'''