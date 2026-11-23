import tempfile
from pathlib import Path
import unittest
from unittest import mock

from tools import configure_device_boot as boot


class BootConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.home = self.root / 'home'
        self.home.mkdir()
        self.app = self.home / "心电 软件'演示"
        self.app.mkdir()
        self.profile = self.home / '.profile'
        self.profile.write_bytes(b'# original\nexport TEST_KEEP=yes\n')
        self.original = self.profile.read_bytes()
        self.dropin = self.root / 'systemd/zz-ecg-standby.conf'
        self.state = self.root / 'state/install.json'

    def install(self):
        return boot.install(self.home, self.app, self.dropin, self.state, 'pi2')

    def test_install_is_idempotent_and_uninstall_preserves_user_edits(self):
        self.install()
        first = self.profile.read_bytes()
        self.install()
        self.assertEqual(first, self.profile.read_bytes())
        self.assertIn(b'--autologin pi2', self.dropin.read_bytes())
        self.assertIn(b'SSH_CONNECTION', first)
        self.assertIn(b'/dev/tty1', first)
        self.assertNotIn(b'exec /bin/sh', first)
        self.profile.write_bytes(first + b'\n# added later\n')
        boot.uninstall(self.state)
        self.assertEqual(self.profile.read_bytes(), self.original + b'\n# added later\n')
        self.assertFalse(self.dropin.exists())

    def test_bash_profile_has_precedence_and_quoted_script_path(self):
        target = self.home / '.bash_profile'
        target.write_text('# bash\n', encoding='utf-8')
        self.install()
        self.assertEqual(self.profile.read_bytes(), self.original)
        self.assertIn("'\"'\"'", target.read_text(encoding='utf-8'))
        boot.uninstall(self.state)
        self.assertEqual(target.read_text(encoding='utf-8'), '# bash\n')

    def test_uninstall_refuses_to_overwrite_modified_system_configuration(self):
        self.install()
        self.dropin.write_text('someone else edited', encoding='utf-8')
        before = self.profile.read_bytes()
        with self.assertRaises(RuntimeError):
            boot.uninstall(self.state)
        self.assertEqual(self.profile.read_bytes(), before)

    def test_unrelated_existing_dropin_is_not_overwritten(self):
        self.dropin.parent.mkdir()
        self.dropin.write_text('existing', encoding='utf-8')
        with self.assertRaises(RuntimeError):
            self.install()
        self.assertEqual(self.profile.read_bytes(), self.original)

    def test_failed_install_can_be_recovered_without_losing_profile(self):
        real = boot.atomic_write
        def fail(path, *args, **kwargs):
            if Path(path) == self.dropin:
                raise OSError('disk full')
            return real(path, *args, **kwargs)
        with mock.patch.object(boot, 'atomic_write', side_effect=fail), self.assertRaises(OSError):
            self.install()
        boot.uninstall(self.state)
        self.assertEqual(self.profile.read_bytes(), self.original)

    def test_invalid_systemd_username_rejected(self):
        with self.assertRaises(ValueError):
            boot.install(self.home, self.app, self.dropin, self.state, 'pi2\nExecStart=evil')


if __name__ == '__main__':
    unittest.main()
