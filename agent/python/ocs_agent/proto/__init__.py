"""Select bindings without mixing protobuf runtimes in a process.

The package root retains the Python 3.5 bindings used by P4App and old SDKs.
Modern generated modules have the same public names and relative imports.
"""

import os

from google.protobuf import __version__ as _protobuf_version


_major = int(_protobuf_version.split('.')[0])
if _major == 7:
    __path__[:] = [os.path.join(os.path.dirname(__file__), 'modern')]
elif _major != 3:
    raise ImportError(
        'Unsupported protobuf {}; install the modern or legacy requirements '
        'as a complete environment'.format(_protobuf_version))
