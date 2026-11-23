"""Local STM32 serial transport discovery; never guess between USB devices."""
import glob
import os

from protocol import BAUD_RATE


def list_serial_ports():
    from serial.tools import list_ports
    stable = {}
    if os.name == "posix":
        for alias in sorted(glob.glob("/dev/serial/by-id/*")):
            stable.setdefault(os.path.realpath(alias), alias)
    result = []
    for item in list_ports.comports():
        path = str(item.device)
        usb = (item.vid is not None or path.startswith(("/dev/ttyUSB", "/dev/ttyACM"))
               or "USB" in str(item.hwid).upper())
        result.append({"device": stable.get(os.path.realpath(path), path),
                       "system_device": path, "description": str(item.description or ""),
                       "usb": usb, "vid": item.vid, "pid": item.pid,
                       "serial_number": item.serial_number})
    return sorted(result, key=lambda item: (not item["usb"], item["device"]))


def resolve_serial_port(value):
    value = str(value or "").strip()
    if not value:
        raise ValueError("请选择串口，或使用 auto 自动选择唯一的 USB 串口。")
    if value.lower() != "auto":
        return value
    ports = [item for item in list_serial_ports() if item["usb"]]
    if not ports:
        raise ValueError("未检测到 USB 串口。请把 USB-TTL 插入树莓派，刷新后重试。")
    if len(ports) != 1:
        choices = "、".join(item["device"] for item in ports)
        raise ValueError("检测到多个 USB 串口，请明确选择连接 STM32 的端口：" + choices)
    return ports[0]["device"]


def open_serial_port(path):
    import serial
    options = {"exclusive": True} if os.name == "posix" else {}
    return serial.Serial(path, BAUD_RATE, timeout=.1, bytesize=8, parity="N", stopbits=1,
                         xonxoff=False, rtscts=False, dsrdtr=False, **options)


def serial_error_message(path, exc):
    detail = str(exc)
    lower = detail.lower()
    if isinstance(exc, PermissionError) or "permission denied" in lower or "access is denied" in lower:
        return f"串口 {path} 无访问权限。请确认 pi2 属于 dialout 组，重新登录后重试。"
    if "exclusive" in lower or "resource temporarily unavailable" in lower or "busy" in lower:
        return f"串口 {path} 正被占用。请先关闭其它串口工具或采集窗口。"
    return f"串口 {path} 连接失败或已断开，请检查 USB-TTL 和接线后重新连接。详情：{detail}"
