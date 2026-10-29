import base64
import os
from pathlib import Path
import stat
import tempfile
import unittest

from scripts.init_env import initialize


class CommunitySetupTests(unittest.TestCase):
    def test_unique_private_credentials_and_preserved_rerun(self):
        with tempfile.TemporaryDirectory() as directory:
            first, second = Path(directory) / 'first.env', Path(directory) / 'second.env'
            self.assertTrue(initialize(first))
            original = first.read_bytes()
            self.assertFalse(initialize(first))
            self.assertEqual(first.read_bytes(), original)
            values = dict(line.split('=', 1) for line in first.read_text().splitlines() if line and not line.startswith('#'))
            self.assertEqual(len(bytes.fromhex(values['POSTGRES_PASSWORD'])), 32)
            self.assertEqual(len(bytes.fromhex(values['APP_BOOTSTRAP_PASSWORD'])), 32)
            self.assertEqual(len(base64.urlsafe_b64decode(values['CONNECTOR_ENCRYPTION_KEYS'])), 32)
            if os.name == 'posix':
                self.assertEqual(stat.S_IMODE(first.stat().st_mode), 0o600)
            self.assertTrue(initialize(second))
            self.assertNotEqual(second.read_bytes(), original)

    def test_does_not_follow_existing_symlink(self):
        if os.name != 'posix':
            self.skipTest('POSIX symlink permissions')
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'target'
            target.write_text('preserve')
            link = Path(directory) / '.env'
            link.symlink_to(target)
            self.assertFalse(initialize(link))
            self.assertEqual(target.read_text(), 'preserve')
