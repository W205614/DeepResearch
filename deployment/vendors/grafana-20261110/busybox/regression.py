"""Real BusyBox HTTP regression against a container-local raw TCP listener."""

import hashlib
import json
import os
from pathlib import Path
import socketserver
import sys
import subprocess
import tempfile
import threading
import time

records = []
accepted = []
lock = threading.Lock()


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def process_request(self, request, client_address):
        with lock:
            accepted.append(client_address)
        super().process_request(request, client_address)


class Handler(socketserver.BaseRequestHandler):
    def handle(self):
        self.request.settimeout(2)
        raw = b""
        while b"\r\n\r\n" not in raw and len(raw) < 65536:
            block = self.request.recv(4096)
            if not block:
                break
            raw += block
        with lock:
            records.append(raw)
        first = raw.split(b"\r\n", 1)[0]
        target = first.split(b" ")[1] if len(first.split(b" ")) > 1 else b""
        locations = {
            b"/redirect-clean": b"/after?x=1",
            b"/redirect-encoded": b"/after%20path?x=%0d%0a",
            b"/redirect-space": b"/after bad",
            b"/redirect-c0": b"/after\tbad",
            b"/redirect-crlf": b"/after\r\nX-Injected: from-response",
            b"/redirect-absolute-space": (
                "http://127.0.0.1:" + str(self.server.server_address[1]) + "/after bad"
            ).encode(),
        }
        if target in locations:
            response = (
                b"HTTP/1.1 302 Found\r\nLocation: "
                + locations[target]
                + b"\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"
            )
        else:
            response = b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nok"
        self.request.sendall(response)


def no_injected_header(raw):
    lines = raw.split(b"\r\n")
    return not any(line.lower().startswith((b"x-injected:", b"x-host-canary:")) for line in lines[1:])


with Server(("127.0.0.1", 0), Handler) as server, tempfile.TemporaryDirectory() as work:
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]
    origin = "http://127.0.0.1:" + str(port)
    results = []

    def run(label, url, *, proxy=False, extra=()):
        executable = "/out/busybox"
        environment = {
            k: v
            for k, v in os.environ.items()
            if k.lower() not in {"http_proxy", "https_proxy", "ftp_proxy", "all_proxy", "no_proxy"}
        }
        if proxy:
            environment["http_proxy"] = origin
        with lock:
            before, len_accepted = len(records), len(accepted)
        process = subprocess.run(
            [executable, "wget", "-q", "-T", "3", "-O", "-", *extra, url],
            cwd=work,
            env=environment,
            capture_output=True,
            timeout=8,
        )
        time.sleep(0.03)
        with lock:
            requests = records[before:]
            connections = len(accepted) - len_accepted
        item = {
            "label": label,
            "variant": "fixed",
            "proxy": proxy,
            "url_hex": url.encode().hex(),
            "exit_code": process.returncode,
            "stdout_hex": process.stdout.hex(),
            "stderr": process.stderr.decode(errors="replace"),
            "connections": connections,
            "requests_hex": [raw.hex() for raw in requests],
        }
        results.append(item)
        print(json.dumps(item), file=sys.stderr, flush=True)
        return process, requests, connections

    injection = "/probe\r\nX-Injected: present\r\nX-Pad: "

    for path in ["/ok", "/encoded%20path?q=%0d%0a%20", "/encoded%00zero?q=%0D%0A", "/plus+query?x=a+b"]:
        process, requests, connections = run("normal-" + path, origin + path)
        assert process.returncode == 0 and process.stdout == b"ok" and len(requests) == 1
        expected = path.split("#", 1)[0].encode()
        assert requests[0].split(b"\r\n", 1)[0] == b"GET " + expected + b" HTTP/1.1"
        assert no_injected_header(requests[0])

    process, requests, connections = run(
        "normal-authentication-space", origin.replace("http://", "http://user:pass word@") + "/ok"
    )
    assert process.returncode == 0 and len(requests) == 1 and no_injected_header(requests[0])
    process, requests, connections = run(
        "normal-post-encoded", origin + "/post%20path", extra=("--post-data", "field=value")
    )
    assert process.returncode == 0 and requests[0].startswith(b"POST /post%20path HTTP/1.1\r\n")

    for byte in [*range(1, 33), 127]:
        process, requests, connections = run(
            "reject-raw-target-" + hex(byte), origin + "/bad" + chr(byte) + "value"
        )
        assert process.returncode != 0 and connections == 0 and not requests, (
            byte,
            process.returncode,
            requests,
        )
        assert b"unencoded control character or space" in process.stderr
    process, requests, connections = run("reject-direct-crlf-injection", origin + injection)
    assert process.returncode != 0 and connections == 0 and not requests

    for path in ["/redirect-clean", "/redirect-encoded"]:
        process, requests, connections = run("normal" + path, origin + path)
        assert (
            process.returncode == 0
            and len(requests) == 2
            and all(no_injected_header(raw) for raw in requests)
        )
        if path == "/redirect-encoded":
            assert requests[1].startswith(b"GET /after%20path?x=%0d%0a HTTP/1.1\r\n")
    for path in ["/redirect-space", "/redirect-absolute-space", "/redirect-c0"]:
        process, requests, connections = run("reject" + path, origin + path)
        assert process.returncode != 0 and len(requests) == 1 and connections == 1
    for path in ["/redirect-crlf"]:
        process, requests, connections = run("existing-header-sanitizer" + path, origin + path)
        assert (
            process.returncode == 0
            and len(requests) == 2
            and all(no_injected_header(raw) for raw in requests)
        )
        assert requests[1].split(b"\r\n", 1)[0] == b"GET /after HTTP/1.1"

    process, requests, connections = run(
        "normal-proxy-encoded", "http://example.invalid/encoded%20path?q=%0d%0a", proxy=True
    )
    assert process.returncode == 0 and len(requests) == 1
    assert requests[0].startswith(b"GET http://example.invalid/encoded%20path?q=%0d%0a HTTP/1.1\r\n")
    for path in ["/raw space", injection]:
        process, requests, connections = run(
            "reject-proxy-target" + path, "http://example.invalid" + path, proxy=True
        )
        assert process.returncode != 0 and not requests and connections == 0

    host_injection = "http://example\r\nX-Host-Canary: present\r\n:80/safe"
    process, requests, connections = run("reject-proxy-host-injection", host_injection, proxy=True)
    assert process.returncode != 0 and not requests and connections == 0

    server.shutdown()
    result = {
        "passed": True,
        "cases": results,
        "case_count": len(results),
        "all_injection_traffic": "Only an in-container127.0.0.1 raw listener; Docker --network none, no host/business mounts.",
        "nul_boundary": "A raw NUL cannot be supplied in an OS argv element; percent-encoded%00 is tested and remains encoded.",
        "redirect_boundary": "Raw TAB and SP in Location survive existing parsing and are rejected before a second connection; relative and absolute redirects are tested. Response CRLF is a header delimiter, not part of Location; the next request is clean without forwarding the injected response header.",
        "binary_sha256": hashlib.sha256(Path("/out/busybox").read_bytes()).hexdigest(),
    }
    print(json.dumps(result, indent=2))
