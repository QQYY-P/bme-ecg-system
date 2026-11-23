import unittest
from types import SimpleNamespace
from unittest import mock

from serial_connection import list_serial_ports, resolve_serial_port, serial_error_message


class SerialConnectionTests(unittest.TestCase):
    def test_auto_does_not_choose_pi_onboard_uart(self):
        with mock.patch("serial_connection.list_serial_ports", return_value=[
                {"device": "/dev/ttyAMA0", "usb": False}]):
            with self.assertRaisesRegex(ValueError, "未检测到 USB"):
                resolve_serial_port("auto")

    def test_auto_requires_exactly_one_usb_adapter(self):
        candidates = [{"device": "/dev/ttyUSB0", "usb": True},
                      {"device": "/dev/ttyUSB1", "usb": True}]
        with mock.patch("serial_connection.list_serial_ports", return_value=candidates):
            with self.assertRaisesRegex(ValueError, "多个 USB"):
                resolve_serial_port("auto")
        with mock.patch("serial_connection.list_serial_ports", return_value=candidates[:1]):
            self.assertEqual(resolve_serial_port(" auto "), "/dev/ttyUSB0")

    def test_explicit_device_is_not_changed_or_auto_detected(self):
        with mock.patch("serial_connection.list_serial_ports") as enumerator:
            self.assertEqual(resolve_serial_port(" /dev/serial/by-id/usb-device "),
                             "/dev/serial/by-id/usb-device")
            self.assertEqual(resolve_serial_port("COM8"), "COM8")
            enumerator.assert_not_called()

    def test_usb_is_prioritized_and_stable_path_is_used(self):
        onboard = SimpleNamespace(device="/dev/ttyAMA0", vid=None, pid=None, hwid="n/a",
                                  description="n/a", serial_number=None)
        usb = SimpleNamespace(device="/dev/ttyUSB0", vid=0x1a86, pid=0x7523, hwid="USB",
                              description="USB Serial", serial_number="123")
        alias = "/dev/serial/by-id/usb-test"
        with mock.patch("serial.tools.list_ports.comports", return_value=[onboard, usb]), \
             mock.patch("serial_connection.os.name", "posix"), \
             mock.patch("serial_connection.glob.glob", return_value=[alias]), \
             mock.patch("serial_connection.os.path.realpath", side_effect=lambda p: "/dev/ttyUSB0" if p == alias else p):
            ports = list_serial_ports()
        self.assertEqual(ports[0]["device"], alias)
        self.assertEqual(ports[0]["system_device"], "/dev/ttyUSB0")
        self.assertTrue(ports[0]["usb"])
        self.assertFalse(ports[1]["usb"])

    def test_permission_and_occupied_port_have_actionable_messages(self):
        self.assertIn("dialout", serial_error_message("/dev/ttyUSB0", PermissionError("permission denied")))
        self.assertIn("占用", serial_error_message("/dev/ttyUSB0", OSError("Could not exclusively lock port")))
        self.assertIn("已断开", serial_error_message("/dev/ttyUSB0", OSError("Input/output error")))


if __name__ == "__main__":
    unittest.main()
