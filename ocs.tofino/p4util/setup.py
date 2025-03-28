from custom_connect import load_config, hostIP, hostMAC, get_switch_port

p4 = bfrt.ocs.pipe

# This function can clear all the tables and later on other fixed objects
# once bfrt support is added.
def clear_all(verbose=True, batching=True):
    global p4
    global bfrt
    
    def _clear(table, verbose=False, batching=False):
        if verbose:
            print("Clearing table {:<40} ... ".
                  format(table['full_name']), end='', flush=True)
        try:    
            entries = table['node'].get(regex=True, print_ents=False)
            try:
                if batching:
                    bfrt.batch_begin()
                for entry in entries:
                    entry.remove()
            except Exception as e:
                print("Problem clearing table {}: {}".format(
                    table['name'], e.sts))
            finally:
                if batching:
                    bfrt.batch_end()
        except Exception as e:
            if e.sts == 6:
                if verbose:
                    print('(Empty) ', end='')
        finally:
            if verbose:
                print('Done')

        # Optionally reset the default action, but not all tables
        # have that
        try:
            table['node'].reset_default()
        except:
            pass
    
    # The order is important. We do want to clear from the top, i.e.
    # delete objects that use other objects, e.g. table entries use
    # selector groups and selector groups use action profile members
    

    # Clear Match Tables
    for table in p4.info(return_info=True, print_info=False):
        if table['type'] in ['MATCH_DIRECT', 'MATCH_INDIRECT_SELECTOR']:
            _clear(table, verbose=verbose, batching=batching)

    # Clear Selectors
    for table in p4.info(return_info=True, print_info=False):
        if table['type'] in ['SELECTOR']:
            _clear(table, verbose=verbose, batching=batching)
            
    # Clear Action Profiles
    for table in p4.info(return_info=True, print_info=False):
        if table['type'] in ['ACTION_PROFILE']:
            _clear(table, verbose=verbose, batching=batching)
    
clear_all()


'''
setup_switch_basic_entries
'''

tb_ipv4_lpm =  p4.SwitchIngress.ipv4_lpm
tb_forward =  p4.SwitchIngress.forward
tb_ocs_mapping = p4.SwitchIngress.ocs_mapping

config = load_config()
num_hosts = config.get('num_hosts', 8)

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

default_pi = [i + 1 if i % 2 == 1 else i - 1 for i in range(1, num_hosts + 1)]
for src_host, dst_host in enumerate(default_pi, 1):  # ingress_port base 1
    tb_ocs_mapping.add_with_NoAction(
        ingress_port      = get_switch_port(src_host),
        ucast_egress_port = get_switch_port(dst_host),
    )

'''
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

tb_ocs_mapping.add_with_NoAction(
    ingress_port=0,
    ucast_egress_port=1
)
tb_ocs_mapping.add_with_NoAction(
    ingress_port=1,
    ucast_egress_port=0
)
'''
    
bfrt.complete_operations()


print("""
******************* PROGAMMING RESULTS *****************
""")
print ("Table ipv4_lpm:")
tb_ipv4_lpm.dump(table=True)
print ("Table tb_forward:")
tb_forward.dump(table=True)