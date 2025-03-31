import bfrtcli
from bfrtcli import bfrt
from logging import info # TODO: 

from config.custom_connect import get_switch_port


def init_ocs_mapping(p4_pipe, default_pi, pi_state, num_hosts):
    if len(default_pi) != num_hosts:
        raise ValueError("The new pi length {} conflicts with  {}".format(len(default_pi), num_hosts))
    if pi_state[0] != 1:
        info("Initialization failed: The OCS is in an unstable state\n")
        return False
    try:
        tb_ocs_mapping = p4_pipe.SwitchIngress.ocs_mapping
        
        # default_pi = [i + 1 if i % 2 == 1 else i - 1 for i in range(1, num_hosts + 1)]
        for src_host, dst_host in enumerate(default_pi, start=1):
            tb_ocs_mapping.add_with_NoAction(
                ingress_port      = get_switch_port(src_host),
                ucast_egress_port = get_switch_port(dst_host),
            )
            
        pi_state[0] = 1
        info("The OCS mapping initialization is complete\n")
        return True
    except ValueError as e:
        info("Initialization Exception:{}\n".format(str(e)))
        pi_state[0] = -1
        return False


def update_ocs_mapping(p4_pipe, new_pi, pi, pi_state, num_hosts):
    """update OCS mapping's table entries"""
    # Pre-check
    if new_pi == pi:
        info("The new config is the same as the current config, skipping updates\n")
        return False
    if len(new_pi) != num_hosts:
        raise ValueError("The new pi length {} conflicts with  {}".format(len(new_pi), num_hosts))
    if pi_state[0] != 1:
        info("The OCS is in the process of updating, rejecting the new request\n")
        return False
    # Enter the update process
    pi_state[0] = -1
    try:
        tb_ocs_mapping = p4_pipe.SwitchIngress.ocs_mapping
        tb_ocs_mapping.clear()

        for src_host, dst_host in enumerate(new_pi, start=1):
            tb_ocs_mapping.add_with_NoAction(
                ingress_port      = get_switch_port(src_host),
                ucast_egress_port = get_switch_port(dst_host),
            )

        pi[:] = new_pi.copy()
        pi_state[0] = 1
        info("OCS update is successful, new mapping:{}\n".format(new_pi))
        return True
    except Exception as e:
        info("Update failed: {}, rolled back\n".format(str(e)))
        pi_state[0] = 1
        return False


'''
# case: 

p4_pipe = bfrt.ocs.pipe
tb_ocs_mapping = p4_pipe.SwitchIngress.ocs_mapping

tb_ocs_mapping.add_with_NoAction(
    ingress_port=0,
    ucast_egress_port=1
)
tb_ocs_mapping.add_with_NoAction(
    ingress_port=1,
    ucast_egress_port=0
)
'''