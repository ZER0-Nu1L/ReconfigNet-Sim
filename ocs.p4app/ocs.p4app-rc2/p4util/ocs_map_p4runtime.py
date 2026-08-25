import time

from config.p4app_config import validate_mapping


MAX_DELAY_MS = 1000
MAX_DELAY_US = MAX_DELAY_MS * 1000


class TableTransitionError(Exception):
    def __init__(self, update_error, rollback_errors=None):
        Exception.__init__(self, str(update_error))
        self.update_error = update_error
        self.rollback_errors = rollback_errors or []

    @property
    def rollback_failed(self):
        return bool(self.rollback_errors)


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


def ocs_entry(ingress_port, egress_port):
    return {
        'table_name': 'egress.ocs_mapping',
        'match_fields': {
            'standard_metadata.ingress_port': [ingress_port],
            'standard_metadata.egress_port': [egress_port],
        },
        'action_name': 'egress.permit_ocs',
        'action_params': {},
    }


def mapping_entries(mapping, num_hosts):
    validate_mapping(mapping, num_hosts)
    return [
        ocs_entry(ingress_port, egress_port)
        for ingress_port, egress_port in enumerate(mapping, start=1)
    ]


def debug_entries(num_hosts):
    return [
        ocs_entry(ingress_port, egress_port)
        for ingress_port in range(1, num_hosts + 1)
        for egress_port in range(1, num_hosts + 1)
        if ingress_port != egress_port
    ]


def entries_for_mode(mode, mapping, num_hosts):
    if mode == 'ocs':
        return mapping_entries(mapping, num_hosts)
    if mode == 'debug':
        return debug_entries(num_hosts)
    raise ValueError("mode must be either debug or ocs")


def _build_p4runtime_entry(switch, entry):
    return switch.p4info_helper.buildTableEntry(
        table_name=entry['table_name'],
        match_fields=entry.get('match_fields'),
        default_action=entry.get('default_action'),
        action_name=entry['action_name'],
        action_params=entry['action_params'],
        priority=entry.get('priority'))


def _insert_table_entry(switch, entry):
    if hasattr(switch, 'p4info_helper') and hasattr(switch, 'sw_conn'):
        switch.sw_conn.WriteTableEntry(_build_p4runtime_entry(switch, entry))
        return
    switch.insertTableEntry(entry)


def _remove_table_entry(switch, entry):
    if hasattr(switch, 'p4info_helper') and hasattr(switch, 'sw_conn'):
        switch.sw_conn.DeleteTableEntry(_build_p4runtime_entry(switch, entry))
        return
    switch.removeTableEntry(entry)


def _rollback_entries(switch, removed_entries, installed_entries):
    rollback_errors = []

    for entry in reversed(installed_entries):
        try:
            _remove_table_entry(switch, entry)
        except Exception as error:
            rollback_errors.append(error)

    for entry in removed_entries:
        try:
            _insert_table_entry(switch, entry)
        except Exception as error:
            rollback_errors.append(error)

    return rollback_errors


def _program_entries(switch, previous_entries, target_entries, delay_us):
    validate_delay_us(delay_us)
    started_ns = monotonic_ns()
    removed_entries = []
    installed_entries = []

    try:
        clear_started_ns = monotonic_ns()
        for entry in previous_entries:
            _remove_table_entry(switch, entry)
            removed_entries.append(entry)
        clear_finished_ns = monotonic_ns()

        gap_started_ns = monotonic_ns()
        if delay_us:
            time.sleep(delay_us / 1000000.0)
        gap_finished_ns = monotonic_ns()

        install_started_ns = monotonic_ns()
        for entry in target_entries:
            _insert_table_entry(switch, entry)
            installed_entries.append(entry)
        install_finished_ns = monotonic_ns()
    except Exception as update_error:
        rollback_errors = _rollback_entries(
            switch, removed_entries, installed_entries)
        raise TableTransitionError(update_error, rollback_errors)

    return {
        'clear_commit_us': (clear_finished_ns - clear_started_ns) // 1000,
        'requested_gap_us': delay_us,
        'actual_gap_us': (gap_finished_ns - gap_started_ns) // 1000,
        'install_commit_us': (install_finished_ns - install_started_ns) // 1000,
        'programming_total_us': (install_finished_ns - started_ns) // 1000,
        'active_entries': len(target_entries),
    }


def unchanged_timing(mode, mapping, num_hosts, delay_us):
    return {
        'clear_commit_us': 0,
        'requested_gap_us': delay_us,
        'actual_gap_us': 0,
        'install_commit_us': 0,
        'programming_total_us': 0,
        'active_entries': len(entries_for_mode(mode, mapping, num_hosts)),
    }


def init_ocs_mapping(switch, initial_mapping, runtime_state, num_hosts):
    validate_mapping(initial_mapping, num_hosts)
    try:
        timing = _program_entries(
            switch, [], mapping_entries(initial_mapping, num_hosts), delay_us=0)
    except TableTransitionError:
        runtime_state['status'] = -2
        raise

    runtime_state['status'] = 1
    runtime_state['mode'] = 'ocs'
    runtime_state['last_timing'] = timing
    print("OCS mapping initialized: {}".format(initial_mapping))
    return timing


def _transition_failure(runtime_state, transition_error, previous_label):
    if transition_error.rollback_failed:
        runtime_state['status'] = -2
        rollback_detail = '; '.join(
            str(error) for error in transition_error.rollback_errors)
        return (False,
                "Update failed and rollback failed: {}; {}".format(
                    transition_error.update_error, rollback_detail),
                None)

    runtime_state['status'] = 1
    return (False,
            "Update failed and previous {} was restored: {}".format(
                previous_label, transition_error.update_error),
            None)


def update_ocs_mode(switch, target_mode, current_mapping,
                    runtime_state, num_hosts, delay_us=0):
    if target_mode not in ('debug', 'ocs'):
        raise ValueError("mode must be either debug or ocs")
    validate_delay_us(delay_us)
    if runtime_state.get('status') != 1:
        return False, 'OCS mapping is not ready', None
    if target_mode == runtime_state.get('mode'):
        return (True, 'unchanged', unchanged_timing(
            target_mode, current_mapping, num_hosts, delay_us))

    previous_mode = runtime_state['mode']
    previous_entries = entries_for_mode(
        previous_mode, current_mapping, num_hosts)
    target_entries = entries_for_mode(
        target_mode, current_mapping, num_hosts)
    runtime_state['status'] = -1

    try:
        timing = _program_entries(
            switch, previous_entries, target_entries, delay_us)
    except TableTransitionError as error:
        return _transition_failure(runtime_state, error, 'mode')

    runtime_state['mode'] = target_mode
    runtime_state['status'] = 1
    runtime_state['last_timing'] = timing
    print("OCS mode updated: {}".format(target_mode))
    return True, 'updated', timing


def update_ocs_mapping(switch, new_mapping, current_mapping,
                       runtime_state, num_hosts, delay_us=0):
    validate_mapping(new_mapping, num_hosts)
    validate_delay_us(delay_us)
    if runtime_state.get('mode') != 'ocs':
        return False, 'OCS mapping cannot be changed while debug mode is active', None
    if new_mapping == current_mapping:
        return (True, 'unchanged', unchanged_timing(
            'ocs', current_mapping, num_hosts, delay_us))
    if runtime_state.get('status') != 1:
        return False, 'OCS mapping is not ready', None

    previous_mapping = list(current_mapping)
    previous_entries = mapping_entries(previous_mapping, num_hosts)
    target_entries = mapping_entries(new_mapping, num_hosts)
    runtime_state['status'] = -1

    try:
        timing = _program_entries(
            switch, previous_entries, target_entries, delay_us)
    except TableTransitionError as error:
        return _transition_failure(runtime_state, error, 'mapping')

    current_mapping[:] = new_mapping
    runtime_state['status'] = 1
    runtime_state['last_timing'] = timing
    print("OCS mapping updated: {}".format(new_mapping))
    return True, 'updated', timing
