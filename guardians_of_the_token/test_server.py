#!/usr/bin/env python3
import argparse
import socket
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


FIXTURES = {
    "/small": (
        10_000,
        "Guardians test URL: small fixture; should pass through.\n",
    ),
    "/large": (
        200_000,
        "Guardians test URL: large fixture; may warn on smaller context windows.\n",
    ),
    "/compact": (
        800_000,
        "Guardians test URL: compact-scale fixture; should trigger guarded fetches.\n",
    ),
}


class GuardiansTestHandler(BaseHTTPRequestHandler):
    server_version = "GuardiansTestHTTP/0.1"

    def do_HEAD(self):
        self.respond(send_body=False)

    def do_GET(self):
        self.respond(send_body=True)

    def log_message(self, format: str, *args):
        return

    def respond(self, *, send_body: bool):
        fixture = FIXTURES.get(self.path)
        if fixture is None:
            body = (
                "Guardians of the Token test server\n"
                "Available paths: /small, /large, /compact\n"
            ).encode()
            self.send_response(404)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if send_body:
                self.wfile.write(body)
            return

        fake_length, text = fixture
        body = (
            "Guardians of the Token HTTP test fixture\n"
            f"Path: {self.path}\n"
            f"HEAD Content-Length: {fake_length:,} bytes\n"
            f"GET body bytes: small readable fixture\n"
            f"Expected behavior: {text}"
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(fake_length if not send_body else len(body)))
        self.end_headers()
        if send_body:
            self.wfile.write(body)


def make_server(host: str, port: int) -> ThreadingHTTPServer:
    return ThreadingHTTPServer((host, port), GuardiansTestHandler)


def local_ipv4_candidates() -> list[str]:
    candidates = []
    try:
        infos = socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET, socket.SOCK_STREAM)
    except OSError:
        infos = []
    for info in infos:
        address = info[4][0]
        if address.startswith("127.") or address in candidates:
            continue
        candidates.append(address)
    return candidates


def main():
    parser = argparse.ArgumentParser(description="Run local Guardians HTTP test fixtures.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()

    server = make_server(args.host, args.port)
    print(f"Serving Guardians test fixtures at http://{args.host}:{args.port}")
    print("Paths: /small, /large, /compact")
    if args.host in {"0.0.0.0", "::"}:
        candidates = local_ipv4_candidates()
        if candidates:
            print("LAN URLs:")
            for address in candidates:
                print(f"  http://{address}:{args.port}")
        else:
            print("LAN URLs: unable to detect a non-loopback IPv4 address on this machine")
    server.serve_forever()


if __name__ == "__main__":
    main()
