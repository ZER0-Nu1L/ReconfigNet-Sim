def setup_switch_basic_entries(p4_pipe, endpoints):
    """Install L3 entries for endpoints whose MAC addresses are known."""
    tb_ipv4_lpm = p4_pipe.SwitchIngress.ipv4_lpm
    tb_forward = p4_pipe.SwitchIngress.forward

    installed = 0
    for endpoint in endpoints:
        if not endpoint.get('mac'):
            print("Skipping L3 entries for slot {} ({}): MAC unknown".format(
                endpoint['slot'], endpoint['name']))
            continue

        tb_ipv4_lpm.add_with_set_nhop(
            dst_addr=endpoint['ipv4'],
            dst_addr_p_length=32,
            nhop_ipv4=endpoint['ipv4'],
            port=endpoint['dev_port'])
        tb_forward.add_with_set_dmac(
            nhop_ipv4=endpoint['ipv4'],
            dmac=endpoint['mac'])
        installed += 1

    return installed


def show_switch_basic_entries(p4_pipe):
    print("Table ipv4_lpm:")
    p4_pipe.SwitchIngress.ipv4_lpm.dump(table=True)

    print("Table forward:")
    p4_pipe.SwitchIngress.forward.dump(table=True)
