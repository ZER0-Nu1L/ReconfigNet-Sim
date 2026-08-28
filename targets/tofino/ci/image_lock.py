#!/usr/bin/env python3
"""Verify or update the immutable Open P4 Studio image lock."""

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


DEFAULT_LOCK = Path('.github/tofino-image-lock.json')
DEFAULT_REPOSITORY = 'ZER0-Nu1L/open-p4studio-container'
EXPECTED_SOURCE_REPOSITORY = (
    'https://github.com/ZER0-Nu1L/open-p4studio-container')
EXPECTED_IMAGE_PREFIX = (
    'ghcr.io/zer0-nu1l/open-p4studio-container@')
EXPECTED_FIELDS = {
    'container_commit',
    'image',
    'open_p4studio_commit',
    'p4c_commit',
    'platform',
    'profile',
    'release',
    'schema_version',
    'sde_version',
    'source_repository',
}
COMMIT_RE = re.compile(r'^[0-9a-f]{40}$')
DIGEST_RE = re.compile(r'^sha256:[0-9a-f]{64}$')
RELEASE_RE = re.compile(
    r'^sde-(?P<sde>[0-9]+\.[0-9]+\.[0-9]+)-tofino1-r[1-9][0-9]*$')


class ImageLockError(RuntimeError):
    pass


def _reject_duplicate_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ImageLockError('duplicate image-lock field: {}'.format(key))
        result[key] = value
    return result


def parse_lock(data):
    try:
        lock = json.loads(
            data.decode('utf-8'), object_pairs_hook=_reject_duplicate_keys)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ImageLockError('invalid image-lock JSON: {}'.format(error))
    if not isinstance(lock, dict):
        raise ImageLockError('image lock must be a JSON object')
    return lock


def validate_lock(lock, expected_release=None):
    fields = set(lock)
    missing = sorted(EXPECTED_FIELDS - fields)
    unexpected = sorted(fields - EXPECTED_FIELDS)
    if missing or unexpected:
        details = []
        if missing:
            details.append('missing {}'.format(', '.join(missing)))
        if unexpected:
            details.append('unexpected {}'.format(', '.join(unexpected)))
        raise ImageLockError('invalid schema fields: {}'.format('; '.join(details)))

    if type(lock['schema_version']) is not int or lock['schema_version'] != 1:
        raise ImageLockError('schema_version must be integer 1')
    if lock['source_repository'] != EXPECTED_SOURCE_REPOSITORY:
        raise ImageLockError('unexpected source_repository')
    if lock['platform'] != 'linux/amd64':
        raise ImageLockError('image lock platform must remain linux/amd64')
    if lock['profile'] != 'tofino1-model-bfrt':
        raise ImageLockError('image lock profile must remain tofino1-model-bfrt')

    release = lock['release']
    if not isinstance(release, str):
        raise ImageLockError('release must be a string')
    release_match = RELEASE_RE.fullmatch(release)
    if not release_match:
        raise ImageLockError('invalid release tag')
    if expected_release and release != expected_release:
        raise ImageLockError(
            'release asset contains {}, expected {}'.format(
                release, expected_release))
    if not isinstance(lock['sde_version'], str):
        raise ImageLockError('sde_version must be a string')
    if lock['sde_version'] != release_match.group('sde'):
        raise ImageLockError('release and sde_version do not match')

    image = lock['image']
    if not isinstance(image, str) or not image.startswith(EXPECTED_IMAGE_PREFIX):
        raise ImageLockError('image lock must use the expected GHCR repository')
    digest = image[len(EXPECTED_IMAGE_PREFIX):]
    if not DIGEST_RE.fullmatch(digest):
        raise ImageLockError('image lock must contain an immutable OCI digest')

    for field in ('container_commit', 'open_p4studio_commit', 'p4c_commit'):
        value = lock[field]
        if not isinstance(value, str) or not COMMIT_RE.fullmatch(value):
            raise ImageLockError('{} must be a full commit SHA'.format(field))
    return lock


def parse_checksums(data):
    try:
        lines = data.decode('utf-8').splitlines()
    except UnicodeDecodeError as error:
        raise ImageLockError('invalid checksums encoding: {}'.format(error))

    result = {}
    for line in lines:
        digest, separator, name = line.partition('  ')
        if not separator or not DIGEST_RE.fullmatch('sha256:' + digest):
            raise ImageLockError('invalid checksum line: {}'.format(line))
        if not name or name in result:
            raise ImageLockError('invalid or duplicate checksum path: {}'.format(name))
        result[name] = digest
    return result


def _asset_url(repository, release, asset):
    quoted_release = urllib.parse.quote(release, safe='.-')
    quoted_asset = urllib.parse.quote(asset, safe='.-')
    return 'https://github.com/{}/releases/download/{}/{}'.format(
        repository, quoted_release, quoted_asset)


def _download(url):
    request = urllib.request.Request(
        url, headers={'User-Agent': 'ReconfigNet-Sim-image-lock/1'})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.read()
    except (urllib.error.URLError, TimeoutError) as error:
        raise ImageLockError('failed to download {}: {}'.format(url, error))


def fetch_release_lock(repository, release, downloader=_download):
    checksums_data = downloader(
        _asset_url(repository, release, 'checksums.sha256'))
    lock_data = downloader(_asset_url(repository, release, 'image-lock.json'))
    checksums = parse_checksums(checksums_data)
    checksum_name = 'release-assets/image-lock.json'
    expected_digest = checksums.get(checksum_name)
    if expected_digest is None:
        raise ImageLockError(
            '{} is absent from checksums.sha256'.format(checksum_name))
    actual_digest = hashlib.sha256(lock_data).hexdigest()
    if actual_digest != expected_digest:
        raise ImageLockError(
            'image-lock checksum mismatch: expected {}, got {}'.format(
                expected_digest, actual_digest))

    lock = validate_lock(parse_lock(lock_data), expected_release=release)
    return lock_data, lock


def verify_local_lock(path, repository=DEFAULT_REPOSITORY, downloader=_download):
    local_data = path.read_bytes()
    local_lock = validate_lock(parse_lock(local_data))
    release_data, release_lock = fetch_release_lock(
        repository, local_lock['release'], downloader=downloader)
    if local_data != release_data or local_lock != release_lock:
        raise ImageLockError(
            '{} is not the exact verified GitHub Release asset'.format(path))
    return local_lock


def update_local_lock(path, release, repository=DEFAULT_REPOSITORY,
                      downloader=_download):
    lock_data, lock = fetch_release_lock(
        repository, release, downloader=downloader)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_name = None
    try:
        with tempfile.NamedTemporaryFile(
                mode='wb', dir=str(path.parent), prefix=path.name + '.',
                delete=False) as temporary:
            temporary.write(lock_data)
            temporary.flush()
            os.fsync(temporary.fileno())
            temporary_name = temporary.name
        os.replace(temporary_name, path)
    finally:
        if temporary_name and os.path.exists(temporary_name):
            os.unlink(temporary_name)
    return lock


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest='command', required=True)

    verify = subparsers.add_parser(
        'verify', help='verify the local lock against its GitHub Release')
    verify.add_argument('--lock', type=Path, default=DEFAULT_LOCK)
    verify.add_argument('--repository', default=DEFAULT_REPOSITORY)

    update = subparsers.add_parser(
        'update', help='replace the local lock with a verified Release asset')
    update.add_argument('--release', required=True)
    update.add_argument('--lock', type=Path, default=DEFAULT_LOCK)
    update.add_argument('--repository', default=DEFAULT_REPOSITORY)
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    try:
        if args.command == 'verify':
            lock = verify_local_lock(args.lock, repository=args.repository)
        else:
            lock = update_local_lock(
                args.lock, args.release, repository=args.repository)
    except (ImageLockError, OSError) as error:
        print('image-lock error: {}'.format(error), file=sys.stderr)
        return 1

    if args.command == 'verify':
        print('TOFINO_IMAGE=' + lock['image'])
    else:
        print('Updated {} from {} release {}'.format(
            args.lock, lock['source_repository'], lock['release']))
    return 0


if __name__ == '__main__':
    sys.exit(main())
