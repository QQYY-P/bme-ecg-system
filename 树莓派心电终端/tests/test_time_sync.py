"""Every operating-system clock command is mocked: tests never change time."""
from datetime import datetime
import subprocess
import unittest
from unittest.mock import patch

import time_sync


def completed(stdout="", stderr="", returncode=0):
    return subprocess.CompletedProcess([], returncode, stdout, stderr)


class TimeSyncTests(unittest.TestCase):
    def setUp(self):
        self.platform = patch("time_sync.platform.system", return_value="Linux")
        self.platform.start()
        self.addCleanup(self.platform.stop)
        self.process = patch("time_sync.subprocess.run")
        self.run = self.process.start()
        self.addCleanup(self.process.stop)
        self.manual = patch("time_sync._MANUAL", None)
        self.manual.start()
        self.addCleanup(self.manual.stop)

    def test_confirmed_ntp_is_trusted_and_has_timezone_timestamps(self):
        self.run.return_value = completed("Timezone=Asia/Shanghai\nNTPSynchronized=yes\nNTP=yes\n")
        result = time_sync.clock_status()
        self.assertTrue(result["trusted"])
        self.assertEqual(result["source"], "ntp")
        self.assertEqual(result["timezone"], "Asia/Shanghai")
        self.assertIsNotNone(datetime.fromisoformat(result["utc"]).tzinfo)
        self.assertIsNotNone(datetime.fromisoformat(result["local"]).tzinfo)
        kwargs = self.run.call_args.kwargs
        self.assertLessEqual(kwargs["timeout"], 5)
        self.assertIs(kwargs["shell"], False)
        self.assertEqual(kwargs["stdin"], subprocess.DEVNULL)
        self.assertIsInstance(self.run.call_args.args[0], list)

    def test_ntp_enabled_does_not_mean_synchronized(self):
        self.run.return_value = completed("NTP=yes\nNTPSynchronized=no\n")
        result = time_sync.clock_status()
        self.assertFalse(result["trusted"])
        self.assertEqual(result["state"], "pending")

    def test_disabled_ntp_stale_sync_flag_not_trusted(self):
        self.run.return_value = completed("NTP=no\nNTPSynchronized=yes\n")
        self.assertFalse(time_sync.clock_status()["trusted"])

    def test_missing_ntp_status_is_not_trusted(self):
        self.run.return_value = completed("NTP=yes\n")
        self.assertFalse(time_sync.clock_status()["trusted"])

    def test_timeout_and_permission_errors_are_not_trusted(self):
        for exc in (subprocess.TimeoutExpired(["timedatectl"], 4), FileNotFoundError("missing")):
            with self.subTest(exc=exc):
                self.run.side_effect = exc
                result = time_sync.clock_status()
                self.assertFalse(result["trusted"])
                self.assertEqual(result["state"], "unknown")

    def test_nonlinux_does_not_claim_verified_or_change_clock(self):
        with patch("time_sync.platform.system", return_value="Windows"):
            self.assertFalse(time_sync.clock_status()["trusted"])
            self.assertEqual(time_sync.request_time_sync()["state"], "unsupported")
            self.assertEqual(time_sync.manual_set_time("2026-10-29 12:30")["state"], "unsupported")
        self.run.assert_not_called()

    def test_enable_ntp_returns_pending_until_confirmed(self):
        self.run.side_effect = [completed(), completed("NTP=yes\nNTPSynchronized=no\n")]
        result = time_sync.request_time_sync()
        self.assertTrue(result["requested"])
        self.assertFalse(result["success"])
        self.assertFalse(result["trusted"])
        self.assertEqual(result["state"], "pending")
        self.assertEqual(self.run.call_args_list[0].args[0], ["sudo", "-n", "timedatectl", "set-ntp", "true"])

    def test_enable_ntp_success_requires_confirmed_status(self):
        self.run.side_effect = [completed(), completed("NTP=yes\nNTPSynchronized=yes\n")]
        result = time_sync.request_time_sync()
        self.assertTrue(result["success"])
        self.assertTrue(result["trusted"])

    def test_sudo_denial_is_reported_without_password_prompt(self):
        self.run.return_value = completed(stderr="sudo: a password is required", returncode=1)
        result = time_sync.request_time_sync()
        self.assertFalse(result["success"])
        self.assertFalse(result["requested"])
        self.assertTrue(result["authorization_required"])
        self.assertIn("password", result["detail"])
        self.assertEqual(self.run.call_count, 1)

    def test_invalid_manual_time_never_executes_commands(self):
        for text in ("2026-02-30 10:00", "2026-10-29 12:30; reboot", "$(reboot)",
                     "2026-10-29T12:30:00Z", "", None):
            with self.subTest(text=text):
                self.assertEqual(time_sync.manual_set_time(text)["state"], "invalid")
        self.run.assert_not_called()

    def test_manual_time_provenance_is_never_called_ntp_verified(self):
        self.run.side_effect = [completed("NTP=yes\nNTPSynchronized=yes\nTimezone=Asia/Shanghai\n"),
                                completed(), completed()]
        result = time_sync.manual_set_time("2026-10-29T12:30")
        self.assertTrue(result["success"])
        self.assertFalse(result["trusted"])
        self.assertEqual(result["source"], "manual")
        self.assertEqual(result["manual"]["requested_local"], "2026-10-29 12:30:00")
        commands = [call.args[0] for call in self.run.call_args_list]
        self.assertEqual(commands[1], ["sudo", "-n", "timedatectl", "set-ntp", "false"])
        self.assertEqual(commands[2], ["sudo", "-n", "timedatectl", "set-time", "2026-10-29 12:30:00"])
        self.run.side_effect = None
        self.run.return_value = completed("NTP=no\nNTPSynchronized=yes\n")
        again = time_sync.clock_status()
        self.assertFalse(again["trusted"])
        self.assertEqual(again["source"], "manual")

    def test_manual_failure_restores_previous_ntp(self):
        self.run.side_effect = [completed("NTP=yes\nNTPSynchronized=yes\n"), completed(),
                                completed(stderr="Permission denied", returncode=1), completed()]
        result = time_sync.manual_set_time("2026-10-29 12:30:20")
        self.assertFalse(result["success"])
        self.assertIsNone(time_sync._MANUAL)
        self.assertEqual(self.run.call_args_list[-1].args[0], ["sudo", "-n", "timedatectl", "set-ntp", "true"])

    def test_manual_when_ntp_disabled_does_not_toggle_it(self):
        self.run.side_effect = [completed("NTP=no\nNTPSynchronized=no\n"), completed()]
        result = time_sync.manual_set_time("2026-10-29 12:30:20")
        self.assertTrue(result["success"])
        self.assertEqual(self.run.call_count, 2)

    def test_disable_ntp_timeout_still_restores_previous_state(self):
        self.run.side_effect = [completed("NTP=yes\nNTPSynchronized=yes\n"),
                                subprocess.TimeoutExpired(["sudo", "timedatectl"], 4), completed()]
        result = time_sync.manual_set_time("2026-10-29 12:30:20")
        self.assertFalse(result["success"])
        self.assertFalse(result["trusted"])
        self.assertTrue(result["outcome_uncertain"])
        self.assertTrue(result["ntp_restore_attempted"])
        self.assertTrue(result["ntp_restore_succeeded"])
        self.assertIn("结果不确定", result["detail"])
        self.assertIsNone(time_sync._MANUAL)
        commands = [call.args[0] for call in self.run.call_args_list]
        self.assertEqual(commands[-1], ["sudo", "-n", "timedatectl", "set-ntp", "true"])
        self.assertFalse(any("set-time" in command for command in commands))
        self.assertTrue(all(call.kwargs["timeout"] == 4 for call in self.run.call_args_list))

    def test_disable_and_restore_timeouts_never_claim_ntp_is_restored(self):
        self.run.side_effect = [completed("NTP=yes\nNTPSynchronized=yes\n"),
                                subprocess.TimeoutExpired(["sudo"], 4),
                                subprocess.TimeoutExpired(["sudo"], 4)]
        result = time_sync.manual_set_time("2026-10-29 12:30:20")
        self.assertFalse(result["success"])
        self.assertTrue(result["outcome_uncertain"])
        self.assertTrue(result["ntp_restore_attempted"])
        self.assertIsNone(result["ntp_restore_succeeded"])
        self.assertIsNone(result["ntp_enabled"])
        self.assertIn("当前开关状态不确定", result["detail"])

    def test_disable_ntp_error_also_attempts_restore(self):
        self.run.side_effect = [completed("NTP=yes\nNTPSynchronized=yes\n"),
                                completed(stderr="D-Bus request failed", returncode=1),
                                completed(stderr="D-Bus request failed", returncode=1)]
        result = time_sync.manual_set_time("2026-10-29 12:30:20")
        self.assertFalse(result["success"])
        self.assertTrue(result["outcome_uncertain"])
        self.assertTrue(result["ntp_restore_attempted"])
        self.assertFalse(result["ntp_restore_succeeded"])
        self.assertEqual(self.run.call_args_list[-1].args[0], ["sudo", "-n", "timedatectl", "set-ntp", "true"])

    def test_temporary_authorization_uses_stdin_without_changing_time(self):
        self.run.return_value = completed()
        secret = "temporary-test-secret"
        result = time_sync.authorize_time(secret)
        self.assertTrue(result["success"])
        self.run.assert_called_once_with(["sudo", "-S", "-p", "", "-v"],
                                         input=secret + "\n", capture_output=True,
                                         text=True, timeout=4, check=False, shell=False)
        self.assertNotIn(secret, repr(result))

    def test_temporary_authorization_invalid_inputs_never_run(self):
        for secret in (None, "", "abc\ndef", "abc\rdef", "abc\0def", "x" * 1025):
            with self.subTest(kind=type(secret).__name__):
                result = time_sync.authorize_time(secret)
                self.assertFalse(result["success"])
        self.run.assert_not_called()

    def test_temporary_authorization_never_echoes_process_output_or_exception(self):
        secret = "private-test-password"
        self.run.return_value = completed(stdout=secret, stderr=secret, returncode=1)
        denied = time_sync.authorize_time(secret)
        self.assertFalse(denied["success"])
        self.assertNotIn(secret, repr(denied))
        for exc in (OSError(secret), subprocess.TimeoutExpired(["sudo"], 4, output=secret)):
            self.run.side_effect = exc
            result = time_sync.authorize_time(secret)
            self.assertFalse(result["success"])
            self.assertNotIn(secret, repr(result))

    def test_temporary_authorization_nonlinux_never_runs(self):
        with patch("time_sync.platform.system", return_value="Windows"):
            self.assertFalse(time_sync.authorize_time("test-password")["success"])
        self.run.assert_not_called()

    def test_authorization_required_only_for_explicit_sudo_prompt_errors(self):
        for message, expected in (("sudo: a terminal is required to read the password", True),
                                  ("sudo: a password is required", True),
                                  ("user is not allowed to execute timedatectl", False),
                                  ("NTP service not supported", False)):
            with self.subTest(message=message):
                self.run.side_effect = None
                self.run.return_value = completed(stderr=message, returncode=1)
                result = time_sync.request_time_sync()
                self.assertEqual(result["authorization_required"], expected)

    def test_manual_sudo_prompt_requests_temporary_authorization(self):
        self.run.side_effect = [completed("NTP=no\nNTPSynchronized=no\n"),
                                completed(stderr="sudo: a password is required", returncode=1)]
        result = time_sync.manual_set_time("2026-10-29 12:30")
        self.assertFalse(result["success"])
        self.assertTrue(result["authorization_required"])


if __name__ == "__main__":
    unittest.main()
