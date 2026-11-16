"""Bounded loopback TCP↔private Unix stream bridge; no HTTP replay or Docker access.

TLS terminates in the existing Nginx frontend over the private Unix socket. This
process never parses credentials, chooses an upstream host or opens outbound TCP.
"""

import argparse
import os
import selectors
import socket
import socketserver
import threading
import time

MAX_CONNECTIONS = 32
IDLE_SECONDS = 30
LIFETIME_SECONDS = 300
MAX_BUFFER = 65536


class Server(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = True
    request_queue_size = MAX_CONNECTIONS

    def __init__(self, port, upstream):
        self.upstream = str(upstream)
        self.budget = threading.BoundedSemaphore(MAX_CONNECTIONS)
        super().__init__(("127.0.0.1", port), Handler)

    def process_request(self, request, client_address):
        if not self.budget.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self.budget.release()
            self.shutdown_request(request)
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.budget.release()

    def handle_error(self, request, client_address):
        # No addresses, headers, URLs or credentials in proxy error output.
        pass


class Handler(socketserver.BaseRequestHandler):
    def handle(self):
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as upstream:
            upstream.settimeout(5)
            try:
                upstream.connect(self.server.upstream)
                # A bounded buffer per direction, backpressure and finite lifetime.
                peers = {self.request: upstream, upstream: self.request}
                pending = {self.request: bytearray(), upstream: bytearray()}
                for stream in peers:
                    stream.setblocking(False)
                reading = set(peers)
                ending = set()
                start = last = time.monotonic()
                with selectors.DefaultSelector() as selector:
                    while time.monotonic() - start < LIFETIME_SECONDS:
                        for stream in list(ending):
                            if not pending[stream]:
                                stream.shutdown(socket.SHUT_WR)
                                ending.remove(stream)
                        if not reading and not any(pending.values()):
                            return
                        for key in list(selector.get_map().values()):
                            selector.unregister(key.fileobj)
                        for stream, other in peers.items():
                            events = (
                                selectors.EVENT_READ
                                if stream in reading
                                and len(pending[other]) < MAX_BUFFER
                                else 0
                            ) | (selectors.EVENT_WRITE if pending[stream] else 0)
                            if events:
                                selector.register(stream, events)
                        for key, events in selector.select(timeout=1):
                            stream = key.fileobj
                            if events & selectors.EVENT_READ:
                                data = stream.recv(
                                    min(16384, MAX_BUFFER - len(pending[peers[stream]]))
                                )
                                if not data:
                                    reading.remove(stream)
                                    ending.add(peers[stream])
                                else:
                                    pending[peers[stream]].extend(data)
                                last = time.monotonic()
                            if events & selectors.EVENT_WRITE:
                                count = stream.send(pending[stream])
                                del pending[stream][:count]
                                last = time.monotonic()
                        if time.monotonic() - last >= IDLE_SECONDS:
                            return
            except (OSError, ValueError):
                return


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--socket", required=True)
    parser.add_argument(
        "--port", type=int, default=int(os.environ.get("NOC_LISTEN_PORT", "18090"))
    )
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535 or not os.path.isabs(args.socket):
        raise SystemExit("Private Unix socket and unprivileged loopback port required")
    with Server(args.port, args.socket) as server:
        server.serve_forever(poll_interval=0.5)


if __name__ == "__main__":
    main()
