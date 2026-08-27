import time

from ocs_agent.errors import ValidationError


MAX_DELAY_US = 1000000


def monotonic_ns():
    try:
        return time.monotonic_ns()
    except AttributeError:
        return int(time.monotonic() * 1000000000)


def validate_delay_us(delay_us):
    if isinstance(delay_us, bool) or not isinstance(delay_us, int):
        raise ValidationError('delay_us must be an integer')
    if not 0 <= delay_us <= MAX_DELAY_US:
        raise ValidationError(
            'delay_us must be between 0 and {}'.format(MAX_DELAY_US))
    return delay_us


class BackendTransitionError(Exception):
    def __init__(self, update_error, rollback_error=None, timing=None):
        Exception.__init__(self, str(update_error))
        self.update_error = update_error
        self.rollback_error = rollback_error
        self.timing = timing or {}

    @property
    def restored(self):
        return self.rollback_error is None


class BackendUnavailableError(Exception):
    def __init__(self, error, timing=None):
        Exception.__init__(self, str(error))
        self.error = error
        self.timing = timing or {}


class BackendPreconditionError(Exception):
    def __init__(self, message, state=None, timing=None):
        Exception.__init__(self, message)
        self.state = state or {}
        self.timing = timing or {}
