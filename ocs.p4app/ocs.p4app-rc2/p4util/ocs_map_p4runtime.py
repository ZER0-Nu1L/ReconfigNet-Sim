from mininet.log import info


def generate_ocs_entries_from_pi(pi, num_hosts):
    """Generate OCS mapping entries with validity verification"""
    if len(pi) != num_hosts:
        raise ValueError("pi length {} does not match {}\n".format(len(pi), num_hosts))
    entries = []
    for ingress_port, egress_port in enumerate(pi, 1):  # ingress_port base 1
        entries.append({
            'table_name': 'egress.ocs_mapping',
            'match_fields': {
                'standard_metadata.ingress_port': [ingress_port],
                'standard_metadata.egress_port': [egress_port]
            },
            'action_name': 'NoAction',
            'action_params': {}
        })
    return entries


def init_ocs_mapping(sw, pi, pi_state, num_hosts):
    if pi_state[0] != 1:
        info("Initialization failed: The OCS is in an unstable state\n")
        return False
    try:
        entries = generate_ocs_entries_from_pi(pi, num_hosts)
        for entry in entries:
            sw.insertTableEntry(entry)
        pi_state[0] = 1
        info("The OCS mapping initialization is complete\n")
        return True
    except ValueError as e:
        info("Initialization Exception:{}\n".format(str(e)))
        pi_state[0] = -1
        return False


def update_ocs_mapping(sw, new_pi, pi, pi_state, num_hosts):
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
        old_entries = generate_ocs_entries_from_pi(pi, num_hosts)
        for entry in old_entries:
            sw.removeTableEntry(entry)

        new_entries = generate_ocs_entries_from_pi(new_pi, num_hosts)
        for entry in new_entries:
            sw.insertTableEntry(entry)

        pi[:] = new_pi.copy()
        pi_state[0] = 1
        info("OCS update is successful, new mapping:{}\n".format(new_pi))
        return True
    except Exception as e:
        info("Update failed: {}, rolled back\n".format(str(e)))
        pi_state[0] = 1
        return False
