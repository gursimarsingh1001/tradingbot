from __future__ import annotations

import http.server
import socketserver
from pathlib import Path


PORT = 4173
DIST_DIR = Path(__file__).resolve().parent / "dist"


class SpaRequestHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(DIST_DIR), **kwargs)

    def do_GET(self) -> None:
        requested = self.translate_path(self.path)
        if self.path == "/" or Path(requested).exists():
            return super().do_GET()

        self.path = "/index.html"
        return super().do_GET()


class ReusableTcpServer(socketserver.TCPServer):
    allow_reuse_address = True


def main() -> None:
    with ReusableTcpServer(("0.0.0.0", PORT), SpaRequestHandler) as httpd:
        httpd.serve_forever()


if __name__ == "__main__":
    main()
