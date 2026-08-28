import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path


CI_DIR = Path(__file__).resolve().parents[1]
if str(CI_DIR) not in sys.path:
    sys.path.insert(0, str(CI_DIR))


import image_lock


class ReleaseFixture(object):
    def __init__(self, lock=None, checksum_override=None):
        self.lock = lock or self.valid_lock()
        self.lock_data = (
            json.dumps(self.lock, indent=2, sort_keys=True) + '\n'
        ).encode('utf-8')
        digest = checksum_override or hashlib.sha256(
            self.lock_data).hexdigest()
        self.checksums_data = (
            '{}  release-assets/image-lock.json\n'.format(digest)
        ).encode('utf-8')

    @staticmethod
    def valid_lock():
        return {
            'container_commit': 'a' * 40,
            'image': (
                'ghcr.io/zer0-nu1l/open-p4studio-container@sha256:'
                + 'b' * 64),
            'open_p4studio_commit': 'c' * 40,
            'p4c_commit': 'd' * 40,
            'platform': 'linux/amd64',
            'profile': 'tofino1-model-bfrt',
            'release': 'sde-9.13.4-tofino1-r3',
            'schema_version': 1,
            'sde_version': '9.13.4',
            'source_repository': (
                'https://github.com/ZER0-Nu1L/open-p4studio-container'),
        }

    def download(self, url):
        if url.endswith('/checksums.sha256'):
            return self.checksums_data
        if url.endswith('/image-lock.json'):
            return self.lock_data
        raise AssertionError(url)


class ImageLockTests(unittest.TestCase):
    def test_verifies_exact_release_asset(self):
        fixture = ReleaseFixture()
        with tempfile.TemporaryDirectory() as temporary_dir:
            path = Path(temporary_dir) / 'image-lock.json'
            path.write_bytes(fixture.lock_data)
            lock = image_lock.verify_local_lock(
                path, downloader=fixture.download)
        self.assertEqual(lock['schema_version'], 1)
        self.assertEqual(lock['release'], 'sde-9.13.4-tofino1-r3')

    def test_rejects_release_checksum_mismatch(self):
        fixture = ReleaseFixture(checksum_override='0' * 64)
        with self.assertRaisesRegex(
                image_lock.ImageLockError, 'checksum mismatch'):
            image_lock.fetch_release_lock(
                image_lock.DEFAULT_REPOSITORY,
                'sde-9.13.4-tofino1-r3',
                downloader=fixture.download)

    def test_rejects_schema_drift(self):
        lock = ReleaseFixture.valid_lock()
        lock['schema_version'] = 2
        with self.assertRaisesRegex(
                image_lock.ImageLockError, 'schema_version'):
            image_lock.validate_lock(lock)

        lock = ReleaseFixture.valid_lock()
        lock['unexpected'] = True
        with self.assertRaisesRegex(
                image_lock.ImageLockError, 'unexpected'):
            image_lock.validate_lock(lock)

    def test_rejects_invalid_release_field_type(self):
        lock = ReleaseFixture.valid_lock()
        lock['release'] = None
        with self.assertRaisesRegex(
                image_lock.ImageLockError, 'release must be a string'):
            image_lock.validate_lock(lock)

    def test_rejects_local_content_not_identical_to_release_asset(self):
        fixture = ReleaseFixture()
        with tempfile.TemporaryDirectory() as temporary_dir:
            path = Path(temporary_dir) / 'image-lock.json'
            path.write_text(
                json.dumps(fixture.lock, separators=(',', ':')),
                encoding='utf-8')
            with self.assertRaisesRegex(
                    image_lock.ImageLockError, 'exact verified'):
                image_lock.verify_local_lock(
                    path, downloader=fixture.download)

    def test_update_writes_verified_release_bytes(self):
        fixture = ReleaseFixture()
        with tempfile.TemporaryDirectory() as temporary_dir:
            path = Path(temporary_dir) / 'nested' / 'image-lock.json'
            lock = image_lock.update_local_lock(
                path, 'sde-9.13.4-tofino1-r3',
                downloader=fixture.download)
            self.assertEqual(path.read_bytes(), fixture.lock_data)
        self.assertEqual(lock, fixture.lock)


if __name__ == '__main__':
    unittest.main()
