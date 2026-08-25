import time

import bfrtcli
from bfrtcli import bfrt

from config.custom_connect import get_switch_port, validate_mapping


MAX_DELAY_MS = 1000
MAX_DELAY_US = MAX_DELAY_MS * 1000


def monotonic_ns():
    try:
        return time.monotonic_ns()
    except AttributeError:
        return int(time.monotonic() * 1000000000)


def validate_delay_ms(delay_ms):
    if isinstance(delay_ms, bool) or not isinstance(delay_ms, int):
        raise ValueError("delay_ms must be an integer")
    if not 0 <= delay_ms <= MAX_DELAY_MS:
        raise ValueError("delay_ms must be between 0 and {}".format(MAX_DELAY_MS))
    return delay_ms


def validate_delay_us(delay_us):
    if isinstance(delay_us, bool) or not isinstance(delay_us, int):
        raise ValueError("delay_us must be an integer")
    if not 0 <= delay_us <= MAX_DELAY_US:
        raise ValueError("delay_us must be between 0 and {}".format(MAX_DELAY_US))
    return delay_us


def mapping_entries(mapping, endpoints):
    return [
        (get_switch_port(endpoints, source_slot),
         get_switch_port(endpoints, destination_slot))
        for source_slot, destination_slot in enumerate(mapping, start=1)
    ]


def debug_entries(endpoints):
    entries = []
    for source in endpoints:
        for destination in endpoints:
            if source['slot'] != destination['slot']:
                entries.append((source['dev_port'], destination['dev_port']))
    return entries


def entries_for_mode(mode, mapping, endpoints):
    if mode == 'ocs':
        return mapping_entries(mapping, endpoints)
    if mode == 'debug':
        return debug_entries(endpoints)
    raise ValueError("mode must be either debug or ocs")


def _program_entries(table, entries, delay_us):
    validate_delay_us(delay_us)
    started_ns = monotonic_ns()

    clear_started_ns = monotonic_ns()
    table.clear()
    bfrt.complete_operations()
    clear_finished_ns = monotonic_ns()

    gap_started_ns = monotonic_ns()
    if delay_us:
        time.sleep(delay_us / 1000000.0)
    gap_finished_ns = monotonic_ns()

    install_started_ns = monotonic_ns()
    for ingress_port, egress_port in entries:
        table.add_with_permit_ocs(
            ingress_port=ingress_port,
            ucast_egress_port=egress_port)
    bfrt.complete_operations()
    install_finished_ns = monotonic_ns()

    return {
        'clear_commit_us': (clear_finished_ns - clear_started_ns) // 1000,
        'requested_gap_us': delay_us,
        'actual_gap_us': (gap_finished_ns - gap_started_ns) // 1000,
        'install_commit_us': (install_finished_ns - install_started_ns) // 1000,
        'programming_total_us': (install_finished_ns - started_ns) // 1000,
        'active_entries': len(entries),
    }


def init_ocs_mapping(p4_pipe, initial_mapping, runtime_state, endpoints):
    validate_mapping(initial_mapping, len(endpoints))
    table = p4_pipe.SwitchIngress.ocs_mapping
    timing = _program_entries(
        table, mapping_entries(initial_mapping, endpoints), delay_us=0)
    runtime_state['status'] = 1
    runtime_state['mode'] = 'ocs'
    runtime_state['last_timing'] = timing
    print("OCS mapping initialized: {}".format(initial_mapping))
    return timing


def _replace_runtime_state(p4_pipe, target_mode, current_mapping,
                           runtime_state, endpoints, delay_us):
    validate_delay_us(delay_us)
    if runtime_state.get('status') != 1:
        return False, 'OCS mapping is not ready', None
    if target_mode == runtime_state.get('mode'):
        timing = {
            'clear_commit_us': 0,
            'requested_gap_us': delay_us,
            'actual_gap_us': 0,
            'install_commit_us': 0,
            'programming_total_us': 0,
            'active_entries': len(entries_for_mode(
                target_mode, current_mapping, endpoints)),
        }
        return True, 'unchanged', timing

    table = p4_pipe.SwitchIngress.ocs_mapping
    previous_mode = runtime_state['mode']
    previous_entries = entries_for_mode(previous_mode, current_mapping, endpoints)
    target_entries = entries_for_mode(target_mode, current_mapping, endpoints)
    runtime_state['status'] = -1

    try:
        timing = _program_entries(table, target_entries, delay_us)
        runtime_state['mode'] = target_mode
        runtime_state['status'] = 1
        runtime_state['last_timing'] = timing
        print("OCS mode updated: {}".format(target_mode))
        return True, 'updated', timing
    except Exception as update_error:
        try:
            _program_entries(table, previous_entries, delay_us=0)
            runtime_state['mode'] = previous_mode
            runtime_state['status'] = 1
            message = "Update failed and previous mode was restored: {}".format(
                update_error)
            print(message)
            return False, message, None
        except Exception as rollback_error:
            runtime_state['status'] = -2
            message = "Update failed and rollback failed: {}; {}".format(
                update_error, rollback_error)
            print(message)
            return False, message, None


def update_ocs_mode(p4_pipe, target_mode, current_mapping,
                    runtime_state, endpoints, delay_us=0):
    if target_mode not in ('debug', 'ocs'):
        raise ValueError("mode must be either debug or ocs")
    return _replace_runtime_state(
        p4_pipe, target_mode, current_mapping,
        runtime_state, endpoints, delay_us)


def update_ocs_mapping(p4_pipe, new_mapping, current_mapping,
                       runtime_state, endpoints, delay_us=0):
    validate_mapping(new_mapping, len(endpoints))
    validate_delay_us(delay_us)
    if runtime_state.get('mode') != 'ocs':
        return False, 'OCS mapping cannot be changed while debug mode is active', None
    if new_mapping == current_mapping:
        timing = {
            'clear_commit_us': 0,
            'requested_gap_us': delay_us,
            'actual_gap_us': 0,
            'install_commit_us': 0,
            'programming_total_us': 0,
            'active_entries': len(new_mapping),
        }
        return True, 'unchanged', timing
    if runtime_state.get('status') != 1:
        return False, 'OCS mapping is not ready', None

    table = p4_pipe.SwitchIngress.ocs_mapping
    previous_mapping = list(current_mapping)
    runtime_state['status'] = -1

    try:
        timing = _program_entries(
            table, mapping_entries(new_mapping, endpoints), delay_us)
        current_mapping[:] = new_mapping
        runtime_state['status'] = 1
        runtime_state['last_timing'] = timing
        print("OCS mapping updated: {}".format(new_mapping))
        return True, 'updated', timing
    except Exception as update_error:
        try:
            _program_entries(
                table, mapping_entries(previous_mapping, endpoints), delay_us=0)
            runtime_state['status'] = 1
            message = "Update failed and previous mapping was restored: {}".format(
                update_error)
            print(message)
            return False, message, None
        except Exception as rollback_error:
            runtime_state['status'] = -2
            message = "Update failed and rollback failed: {}; {}".format(
                update_error, rollback_error)
            print(message)
            return False, message, None
