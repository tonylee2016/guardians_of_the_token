from __future__ import annotations

import argparse
import html
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from string import Template

from guardians_of_the_token.report import report_data

_TEMPLATE = Template((Path(__file__).parent / "dashboard.html").read_text())


def dashboard_html(data: dict) -> str:
    targets = "\n".join(
        f"<li><span>{html.escape(target)}</span><strong>~{tokens:,} tokens</strong></li>"
        for target, tokens in data["top_targets"]
    ) or "<li><span>No events yet.</span><strong>0 tokens</strong></li>"
    clients = "\n".join(
        f"<li><span>{html.escape(name)}</span><strong>{count:,}</strong></li>"
        for name, count in sorted(data["by_client"].items())
    ) or "<li><span>No events yet.</span><strong>0</strong></li>"
    return _TEMPLATE.substitute(
        tokens_saved=f"{data['tokens_saved']:,}",
        cost_saved=f"${data['cost_saved']:.4f}",
        blocked_operations=f"{data['blocked_operations']:,}",
        targets=targets,
        clients=clients,
        raw_data=html.escape(json.dumps(data, indent=2)),
    )


def make_handler(base_dir: Path):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path not in {"/", "/index.html"}:
                self.send_response(404)
                self.end_headers()
                return
            body = dashboard_html(report_data(base_dir)).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args):
            return

    return Handler


def main():
    parser = argparse.ArgumentParser(description="Run the local Guardians dashboard.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--project", default=".")
    args = parser.parse_args()

    base_dir = Path(args.project).expanduser().resolve()
    server = ThreadingHTTPServer((args.host, args.port), make_handler(base_dir))
    print(f"Guardians dashboard: http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
