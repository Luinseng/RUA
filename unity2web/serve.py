"""Local web server for Unity WebGL builds.

Serves Unity-compressed files (.gz/.br/.unityweb) with the right Content-Encoding and MIME type,
negotiates the .gz/.br sidecars written by 'compress', supports byte ranges (video/audio seeking)
and can send cross-origin isolation headers for multithreaded builds.
"""

from __future__ import annotations

import email.utils
import mimetypes
import os
import posixpath
import urllib.parse
from functools import partial
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

TYPES = {
    ".wasm": "application/wasm",
    ".js": "application/javascript",
    ".data": "application/octet-stream",
    ".symbols.json": "application/octet-stream",
    ".json": "application/json",
    ".html": "text/html; charset=utf-8",
    ".unityweb": "application/octet-stream",
}


def content_type(name: str) -> str:
    for suffix, mime in TYPES.items():
        if name.endswith(suffix):
            return mime
    return mimetypes.guess_type(name)[0] or "application/octet-stream"


class UnityHandler(SimpleHTTPRequestHandler):
    isolate = False

    def log_message(self, fmt: str, *args) -> None:
        if os.environ.get("U2W_SERVE_QUIET") != "1":
            super().log_message(fmt, *args)

    def end_headers(self) -> None:
        self.send_header("Cache-Control", "no-cache")
        if self.isolate:
            self.send_header("Cross-Origin-Opener-Policy", "same-origin")
            self.send_header("Cross-Origin-Embedder-Policy", "require-corp")
            self.send_header("Cross-Origin-Resource-Policy", "cross-origin")
        super().end_headers()

    def _resolve(self) -> Path | None:
        path = urllib.parse.unquote(urllib.parse.urlsplit(self.path).path)
        path = posixpath.normpath(path).lstrip("/")
        root = Path(self.directory).resolve()
        target = (root / path).resolve()
        if root != target and root not in target.parents:
            return None
        if target.is_dir():
            target = target / "index.html"
        return target

    def do_HEAD(self) -> None:
        self._serve(head=True)

    def do_GET(self) -> None:
        self._serve(head=False)

    def _serve(self, head: bool) -> None:
        target = self._resolve()
        if target is None:
            self.send_error(HTTPStatus.FORBIDDEN)
            return
        encoding = None
        name = target.name
        if not target.exists():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        # Files Unity compressed itself carry the encoding in their extension.
        if name.endswith(".gz"):
            encoding, name = "gzip", name[:-3]
        elif name.endswith(".br"):
            encoding, name = "br", name[:-3]
        elif name.endswith(".unityweb"):
            with open(target, "rb") as fh:
                encoding = "gzip" if fh.read(2) == b"\x1f\x8b" else None
        else:
            accepted = self.headers.get("Accept-Encoding", "")
            for sidecar, enc in ((".br", "br"), (".gz", "gzip")):
                candidate = target.with_name(target.name + sidecar)
                if enc in accepted and candidate.exists() and candidate.stat().st_mtime >= target.stat().st_mtime:
                    target, encoding = candidate, enc
                    break

        size = target.stat().st_size
        start, end = 0, size - 1
        status = HTTPStatus.OK
        range_header = self.headers.get("Range")
        if range_header and encoding is None and range_header.startswith("bytes="):
            first, _, last = range_header[6:].split(",")[0].partition("-")
            try:
                if first:
                    start = int(first)
                    end = int(last) if last else size - 1
                else:
                    start = max(0, size - int(last))
                end = min(end, size - 1)
                if start > end:
                    raise ValueError
                status = HTTPStatus.PARTIAL_CONTENT
            except ValueError:
                self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
                self.send_header("Content-Range", f"bytes */{size}")
                self.end_headers()
                return

        self.send_response(status)
        self.send_header("Content-Type", content_type(name))
        if encoding:
            self.send_header("Content-Encoding", encoding)
            self.send_header("Vary", "Accept-Encoding")
        else:
            self.send_header("Accept-Ranges", "bytes")
        if status == HTTPStatus.PARTIAL_CONTENT:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.send_header("Content-Length", str(end - start + 1))
        self.send_header("Last-Modified", email.utils.formatdate(target.stat().st_mtime, usegmt=True))
        self.end_headers()
        if head:
            return
        with open(target, "rb") as fh:
            fh.seek(start)
            remaining = end - start + 1
            try:
                while remaining:
                    chunk = fh.read(min(remaining, 1 << 20))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)
            except (BrokenPipeError, ConnectionResetError):
                pass


def make_server(directory: Path, port: int = 8080, host: str = "127.0.0.1", isolate: bool = False) -> ThreadingHTTPServer:
    handler = partial(UnityHandler, directory=str(directory))
    UnityHandler.isolate = isolate
    return ThreadingHTTPServer((host, port), handler)


def serve(directory: Path, port: int = 8080, host: str = "127.0.0.1", isolate: bool = False) -> None:
    server = make_server(directory, port, host, isolate)
    print(f"serving {directory} at http://{host}:{server.server_address[1]}/  (Ctrl+C to stop)", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
