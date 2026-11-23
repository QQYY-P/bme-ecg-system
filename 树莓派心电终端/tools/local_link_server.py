"""电脑上的局域网连通性测试接口，仅接收演示消息，不接收心电/身份数据。"""
import argparse
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
import ipaddress
import json
from pathlib import Path
import re


class LinkServer(HTTPServer):
    def __init__(self, address, output):
        self.output = Path(output)
        self.output.mkdir(parents=True, exist_ok=True)
        super().__init__(address, Handler)

    def get_request(self):
        sock, address = super().get_request()
        sock.settimeout(5)
        return sock, address


class Handler(BaseHTTPRequestHandler):
    server_version = "ECGLocalLink/1.0"

    def reply(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/health":
            self.reply(200, {"ok": True, "service": "ecg-local-link", "mode": "demo_only"})
        else:
            self.reply(404, {"error": "not_found"})

    def do_POST(self):
        if self.path != "/api/demo":
            self.reply(404, {"error": "not_found"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 1024:
                raise ValueError("body_must_be_1_to_1024_bytes")
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            if not isinstance(payload, dict) or set(payload) != {"device_id", "message"}:
                raise ValueError("expected_device_id_and_message_only")
            if not isinstance(payload["device_id"], str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,32}", payload["device_id"]):
                raise ValueError("invalid_device_id")
            if not isinstance(payload["message"], str) or len(payload["message"]) > 120:
                raise ValueError("invalid_demo_message")
        except (ValueError, UnicodeError, TimeoutError) as exc:
            self.reply(400, {"error": str(exc)})
            return
        record = dict(payload, source="demo", received_at=datetime.now(timezone.utc).isoformat())
        path = self.server.output / "latest_demo.json"
        try:
            temp = path.with_suffix(".tmp")
            temp.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
            temp.replace(path)
        except OSError:
            self.reply(500, {"error": "storage_error"})
            return
        self.reply(200, {"ok": True, "received": record})


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bind", default="127.0.0.1", help="仅绑定指定本机地址")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--output", type=Path, default=Path(__file__).resolve().parents[1] / "validation" / "local_link")
    args = parser.parse_args()
    address = ipaddress.ip_address(args.bind)
    if address.is_unspecified or not (address.is_private or address.is_loopback):
        parser.error("请指定一个本机局域网或回环地址，不使用通配地址")
    if not 1 <= args.port <= 65535:
        parser.error("端口范围应为 1-65535")
    with LinkServer((args.bind, args.port), args.output) as server:
        print("Local demo endpoint: http://%s:%d/health" % server.server_address, flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
