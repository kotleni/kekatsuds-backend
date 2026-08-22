#!/usr/bin/env python3
"""HTTP backend that hosts Nintendo DS ROMs from ROMS_DIR.

Endpoints:
    GET  /roms            - ROM list in TSV format (see example.txt)
    GET  /roms/<filename> - download a ROM file
    GET  /roms/icon.png   - shared ROM icon image
    HEAD                  - supported on all of the above

Files are streamed chunk by chunk with an accurate Content-Length header
so clients like Kekatsu-DS can display live download progress.
"""

import os
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import quote, unquote

HOST = "0.0.0.0"
PORT = 8888

ROMS_DIR = "roms"
ROM_EXTENSIONS = (".nds", ".zip")

ICON_PATH = "banner.png"

DB_VERSION = "1"
DB_HEADER_LINE = "\t"
SYSTEM = "nds"
REGION = "ANY"
STUB = "todo"

ROMS_ENDPOINT = "/roms"
DOWNLOAD_PREFIX = "/roms/"
ICON_ENDPOINT = "/roms/icon.png"

SERVER_NAME = "kekatsu-backend"
CHUNK_SIZE = 512 * 1024


@dataclass
class RomEntry:
    title: str
    filename: str
    size: int


def scan_roms():
    entries = []
    for name in sorted(os.listdir(ROMS_DIR)):
        path = os.path.join(ROMS_DIR, name)
        if not os.path.isfile(path):
            continue
        if not name.lower().endswith(ROM_EXTENSIONS):
            continue
        entries.append(RomEntry(
            title=os.path.splitext(name)[0],
            filename=name,
            size=os.path.getsize(path),
        ))
    return entries


def build_rom_list(base_url):
    icon_url = f"{base_url}{ICON_ENDPOINT}"
    lines = [DB_VERSION, DB_HEADER_LINE]
    for rom in ROMS:
        download_url = f"{base_url}{DOWNLOAD_PREFIX}{quote(rom.filename)}"
        fields = [
            rom.title,
            SYSTEM,
            REGION,
            STUB,
            STUB,
            download_url,
            rom.filename,
            str(rom.size),
            icon_url,
        ]
        lines.append("\t".join(fields))
    return ("\n".join(lines) + "\n").encode("utf-8")


class RomRequestHandler(BaseHTTPRequestHandler):

    server_version = SERVER_NAME

    def _base_url(self):
        host = self.headers.get("Host")
        if host:
            return f"http://{host}"
        return f"http://{HOST}:{PORT}"

    def _start_response(self, code, content_type, content_length, filename=None):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(content_length))
        self.send_header("Connection", "close")
        self.close_connection = True
        if filename:
            self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.end_headers()

    def _send_bytes(self, data, content_type, filename=None):
        self._start_response(200, content_type, len(data), filename)
        if self.command != "HEAD":
            self.wfile.write(data)

    def _send_error(self, code, message):
        body = message.encode("utf-8")
        self._start_response(code, "text/plain; charset=utf-8", len(body))
        if self.command != "HEAD":
            self.wfile.write(body)

    def _send_fs_file(self, fs_path, content_type, download_name=None):
        try:
            size = os.path.getsize(fs_path)
            f = open(fs_path, "rb")
        except OSError:
            self._send_error(404, "Not Found")
            return
        with f:
            self._start_response(200, content_type, size, download_name)
            if self.command == "HEAD":
                return
            remaining = size
            while remaining > 0:
                chunk = f.read(min(CHUNK_SIZE, remaining))
                if not chunk:
                    break
                try:
                    self.wfile.write(chunk)
                except OSError:
                    return  # client disconnected or cancelled the download
                remaining -= len(chunk)

    def _route(self):
        path = unquote(self.path.split("?", 1)[0])

        if path in (ROMS_ENDPOINT, ROMS_ENDPOINT + "/"):
            self._send_bytes(build_rom_list(self._base_url()), "text/plain; charset=utf-8")
            return

        if path == ICON_ENDPOINT:
            self._send_fs_file(ICON_PATH, "image/png")
            return

        if path.startswith(DOWNLOAD_PREFIX) and len(path) > len(DOWNLOAD_PREFIX):
            name = path[len(DOWNLOAD_PREFIX):]
            if "/" in name or "\\" in name or "\x00" in name:
                self._send_error(400, "Bad Request")
                return
            fs_path = os.path.join(ROMS_DIR, name)
            if os.path.isfile(fs_path):
                self._send_fs_file(fs_path, "application/octet-stream", name)
                return

        self._send_error(404, "Not Found")

    def do_GET(self):
        self._route()

    def do_HEAD(self):
        self._route()

    def log_message(self, fmt, *args):
        print(f"[{self.log_date_time_string()}] {self.address_string()} {fmt % args}", flush=True)


def main():
    global ROMS
    if not os.path.isdir(ROMS_DIR):
        raise SystemExit(f"ROMs directory not found: {ROMS_DIR}")
    if not os.path.isfile(ICON_PATH):
        raise SystemExit(f"Icon file not found: {ICON_PATH}")

    ROMS = scan_roms()
    print(f"Found {len(ROMS)} ROM(s) in '{ROMS_DIR}'", flush=True)

    server = ThreadingHTTPServer((HOST, PORT), RomRequestHandler)
    print(f"Serving on http://{HOST}:{PORT}{ROMS_ENDPOINT}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
