#!/usr/bin/env python3
"""HTTP backend that hosts ROMs for multiple consoles from ROMS_DIR.

Endpoints:
    GET  /roms                       - ROM list in TSV format (see example.txt)
    GET  /roms/<system>/<filename>   - download a ROM file
    GET  /roms/icon.png              - shared ROM icon image
    HEAD                             - supported on all of the above

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

# Console configuration: system ID -> (subfolder under ROMS_DIR, ROM extensions)
CONSOLES = {
    "gba": ("gba", (".gba", ".zip")),
    "nds": ("nds", (".nds", ".zip")),
    "dsi": ("dsi", (".dsi", ".nds", ".zip")),
}

ICON_PATH = "banner.png"

DB_VERSION = "1"
DB_HEADER_LINE = "\t"
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
    system: str


def ensure_rom_dirs():
    os.makedirs(ROMS_DIR, exist_ok=True)
    for system, (folder, _) in CONSOLES.items():
        console_dir = os.path.join(ROMS_DIR, folder)
        if not os.path.isdir(console_dir):
            os.makedirs(console_dir)
            print(f"Created folder for '{system}': {console_dir}", flush=True)


def scan_roms():
    entries = []
    for system, (folder, extensions) in CONSOLES.items():
        console_dir = os.path.join(ROMS_DIR, folder)
        for name in sorted(os.listdir(console_dir)):
            path = os.path.join(console_dir, name)
            if not os.path.isfile(path):
                continue
            if not name.lower().endswith(extensions):
                continue
            entries.append(RomEntry(
                title=os.path.splitext(name)[0],
                filename=name,
                size=os.path.getsize(path),
                system=system,
            ))
    return entries


def build_rom_list(base_url):
    icon_url = f"{base_url}{ICON_ENDPOINT}"
    lines = [DB_VERSION, DB_HEADER_LINE]
    for rom in ROMS:
        download_url = f"{base_url}{DOWNLOAD_PREFIX}{quote(rom.system)}/{quote(rom.filename)}"
        fields = [
            rom.title,
            rom.system,
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
            rest = path[len(DOWNLOAD_PREFIX):]
            parts = rest.split("/", 1)
            if len(parts) == 2:
                system, name = parts
                if (system in CONSOLES and name
                        and "/" not in name and "\\" not in name and "\x00" not in name):
                    fs_path = os.path.join(ROMS_DIR, CONSOLES[system][0], name)
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
    if not os.path.isfile(ICON_PATH):
        raise SystemExit(f"Icon file not found: {ICON_PATH}")

    ensure_rom_dirs()
    ROMS = scan_roms()
    counts = {}
    for rom in ROMS:
        counts[rom.system] = counts.get(rom.system, 0) + 1
    summary = ", ".join(f"{system}: {count}" for system, count in sorted(counts.items()))
    print(f"Found {len(ROMS)} ROM(s) ({summary or 'none'})", flush=True)

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
