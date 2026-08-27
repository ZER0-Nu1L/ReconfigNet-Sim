import time

from bfrtcli import bfrt

from config.device_profile import get_switch_port, validate_mapping


def monotonic_ns():
    try:
        return time.monotonic_ns()
    except AttributeError:
        return int(time.monotonic() * 1000000000)


def mapping_entries(mapping, endpoints):
    return [
        (get_switch_port(endpoints, source_slot),
         get_switch_port(endpoints, destination_slot))
        for source_slot, destination_slot in enumerate(mapping, start=1)
    ]


def init_ocs_mapping(p4_pipe, initial_mapping, runtime_state, endpoints):
    """Install the startup mapping before a formal Agent owns the device."""
    validate_mapping(initial_mapping, len(endpoints))
    table = p4_pipe.SwitchIngress.ocs_mapping
    entries = mapping_entries(initial_mapping, endpoints)
    started_ns = monotonic_ns()

    clear_started_ns = monotonic_ns()
    table.clear()
    bfrt.complete_operations()
    clear_finished_ns = monotonic_ns()

    install_started_ns = monotonic_ns()
    for ingress_port, egress_port in entries:
        table.add_with_permit_ocs(
            ingress_port=ingress_port,
            ucast_egress_port=egress_port)
    bfrt.complete_operations()
    install_finished_ns = monotonic_ns()

    timing = {
        'clear_commit_us': (
            clear_finished_ns - clear_started_ns) // 1000,
        'requested_gap_us': 0,
        'actual_gap_us': 0,
        'install_commit_us': (
            install_finished_ns - install_started_ns) // 1000,
        'programming_total_us': (
            install_finished_ns - started_ns) // 1000,
        'active_entries': len(entries),
    }
    runtime_state['status'] = 1
    runtime_state['mode'] = 'ocs'
    runtime_state['last_timing'] = timing
    print("OCS mapping initialized: {}".format(initial_mapping))
    return timing
