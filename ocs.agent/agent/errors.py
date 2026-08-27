class OcsError(Exception):
    code = 'UNKNOWN'

    def __init__(self, message, details=None):
        Exception.__init__(self, message)
        self.message = message
        self.details = details or {}
        self.request_id = None
        self.timing = None


class ValidationError(OcsError):
    code = 'INVALID_ARGUMENT'


class ConflictError(OcsError):
    code = 'FAILED_PRECONDITION'

    def __init__(self, port_name, connection_name):
        OcsError.__init__(
            self,
            'Port {} is already used by connection {}'.format(
                port_name, connection_name),
            {
                'port_name': port_name,
                'connection_name': connection_name,
            })


class FailedPreconditionError(OcsError):
    code = 'FAILED_PRECONDITION'


class RevisionConflictError(OcsError):
    code = 'ABORTED'


class NotFoundError(OcsError):
    code = 'NOT_FOUND'


class UnsupportedError(OcsError):
    code = 'UNIMPLEMENTED'


class UnavailableError(OcsError):
    code = 'UNAVAILABLE'


class ResourceExhaustedError(OcsError):
    code = 'RESOURCE_EXHAUSTED'


class ApplyError(OcsError):
    code = 'ABORTED'

    def __init__(self, message, restored, details=None):
        OcsError.__init__(self, message, details)
        self.restored = bool(restored)
        if not self.restored:
            self.code = 'INTERNAL'
