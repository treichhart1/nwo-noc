"""Isolated CI-only HTTPS/DNS public contract fixture; never deploy this server."""

import http.server
import json
import socketserver
import ssl
import struct
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path


class DNS(socketserver.BaseRequestHandler):
    def handle(self):
        data, sock = self.request
        end = 12
        labels = []
        while data[end]:
            n = data[end]
            labels.append(data[end + 1 : end + 1 + n].decode("ascii"))
            end += n + 1
        end += 1
        kind = struct.unpack("!H", data[end : end + 2])[0]
        question = data[12 : end + 4]
        okay = ".".join(labels) == "status.noc128.test" and kind == 1
        header = data[:2] + struct.pack("!HHHHH", 0x8180, 1, int(okay), 0, 0)
        answer = (
            b"\xc0\x0c" + struct.pack("!HHIH", 1, 1, 30, 4) + bytes([93, 184, 216, 34])
            if okay
            else b""
        )
        sock.sendto(header + question + answer, self.client_address)


class Gateway(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        mode = Path("/fixtures/mode").read_text().strip()
        if mode == "down":
            self.send_error(503)
            return
        if mode == "redirect":
            self.send_response(302)
            self.send_header("Location", "http://127.0.0.1/private")
            self.end_headers()
            return
        now = datetime.now(timezone.utc)
        stamp = lambda value: value.isoformat()
        feed = dict(
            api_version="2",
            overall_status="all_systems_operational",
            freshness="fresh",
            generated_at=stamp(now),
            expires_at=stamp(now + timedelta(seconds=90)),
            last_public_update=stamp(now),
            basis="administrator-approved announcements; not live service verification",
            service_areas=[],
            incidents=[],
            maintenance=[],
            resolved=[],
            next_history_offset=None,
            revision_token="a" * 64,
        )
        if mode == "outage":
            feed["overall_status"] = "major_service_interruption"
            feed["revision_token"] = "b" * 64
            feed["incidents"] = [
                dict(
                    id="11111111-1111-4111-8111-111111111111",
                    title="Synthetic approved outage",
                    description="Technicians are investigating a synthetic interruption.",
                    status="service_disruption",
                    areas=["Synthetic community"],
                    starts_at=None,
                    ends_at=None,
                    estimated_restoration_at=None,
                    updated_at=stamp(now),
                    impact="major",
                    area_details=[],
                    display_status="major_service_interruption",
                )
            ]
        if mode == "stale":
            feed.update(
                overall_status="unknown",
                freshness="stale",
                last_public_update=stamp(now - timedelta(days=2)),
            )
        if mode == "private":
            feed["customer_phone"] = "forbidden"
        body = json.dumps(feed).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


if __name__ == "__main__":
    dns = socketserver.UDPServer(("0.0.0.0", 53), DNS)
    threading.Thread(target=dns.serve_forever, daemon=True).start()
    server = http.server.ThreadingHTTPServer(("0.0.0.0", 443), Gateway)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.load_cert_chain("/fixtures/cert.pem", "/fixtures/key.pem")
    server.socket = ctx.wrap_socket(server.socket, server_side=True)
    server.serve_forever()
