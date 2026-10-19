#!/usr/bin/env python3
"""Reproduce both Python binding sets with their pinned compiler environments."""

from __future__ import print_function

import argparse
import hashlib
import os
import re
import subprocess
import sys
import tempfile


HERE = os.path.dirname(os.path.abspath(__file__))
VENDOR = os.path.join(HERE, 'vendor', 'openconfig-gnmi-v0.14.1')
PACKAGE = os.path.join(HERE, '..', 'python', 'ocs_agent', 'proto')
SOURCES = ('ocs_operations.proto', 'device_backend.proto',
           'gnmi.proto', 'gnmi_ext.proto')


def verify_sources():
    with open(os.path.join(VENDOR, 'SHA256SUMS'), encoding='utf-8') as stream:
        for line in stream:
            expected, name = line.split()
            with open(os.path.join(VENDOR, name), 'rb') as source:
                observed = hashlib.sha256(source.read()).hexdigest()
            if expected != observed:
                raise ValueError('Vendored source checksum mismatch: ' + name)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--flavor', choices=('legacy', 'modern'),
                        default='modern')
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    try:
        from importlib.metadata import version
    except ImportError:
        from pkg_resources import get_distribution
        version = lambda name: get_distribution(name).version
    expected = '1.40.0' if args.flavor == 'legacy' else '1.84.0'
    if version('grpcio-tools') != expected:
        parser.error('Use grpcio-tools==' + expected + ' for ' + args.flavor)
    verify_sources()
    destination = PACKAGE
    if args.flavor == 'modern':
        destination = os.path.join(PACKAGE, 'modern')
    with tempfile.TemporaryDirectory(prefix='ocs-proto-') as temporary:
        inputs = os.path.join(temporary, 'input')
        output = os.path.join(temporary, 'output')
        os.makedirs(inputs)
        os.makedirs(output)
        for name in SOURCES:
            source = VENDOR if name.startswith('gnmi') else HERE
            with open(os.path.join(source, name), encoding='utf-8') as stream:
                content = stream.read()
            # Preserve the historical flat descriptor names and Python API.
            content = content.replace(
                'github.com/openconfig/gnmi/proto/gnmi_ext/gnmi_ext.proto',
                'gnmi_ext.proto')
            with open(os.path.join(inputs, name), 'w', encoding='utf-8') as stream:
                stream.write(content)
        subprocess.check_call([
            sys.executable, '-m', 'grpc_tools.protoc', '-I', inputs,
            '--python_out=' + output, '--grpc_python_out=' + output,
        ] + [os.path.join(inputs, name) for name in SOURCES])
        os.makedirs(destination, exist_ok=True)
        changed = []
        for name in sorted(os.listdir(output)):
            path = os.path.join(output, name)
            with open(path, encoding='utf-8') as stream:
                content = stream.read()
            content = re.sub(r'^import (\w+_pb2) as ',
                             r'from . import \1 as ', content,
                             flags=re.MULTILINE)
            content = content.rstrip() + '\n'
            target = os.path.join(destination, name)
            previous = None
            if os.path.exists(target):
                with open(target, encoding='utf-8') as stream:
                    previous = stream.read()
            if previous != content:
                changed.append(os.path.relpath(target, HERE))
                if not args.check:
                    with open(target, 'w', encoding='utf-8') as stream:
                        stream.write(content)
        if args.check and changed:
            raise SystemExit('Bindings differ: ' + ', '.join(changed))
        print('{} bindings reproducible ({} updated)'.format(
            args.flavor, len(changed)))


if __name__ == '__main__':
    main()
