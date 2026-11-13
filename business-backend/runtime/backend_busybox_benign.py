"""Exercise ordinary BusyBox behavior without adversarial URLs or external traffic."""
import hashlib
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
from pathlib import Path
import subprocess
import threading


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = b'backend-busybox-normal-response\n'
        self.send_response(200)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_):
        pass


def main():
    binary = Path('/out/busybox')
    applets = subprocess.check_output([str(binary), '--list'], text=True).splitlines()
    if not {'sh', 'wget', 'printf', 'sha256sum'} <= set(applets):
        raise RuntimeError('Required ordinary applets are missing')
    shell = subprocess.check_output([str(binary), 'sh', '-c', 'value=backend; printf "%s\\n" "$value"'])
    if shell != b'backend\n':
        raise RuntimeError('Ordinary shell behavior differs')
    server = HTTPServer(('127.0.0.1', 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        body = subprocess.check_output([str(binary), 'wget', '-q', '-O', '-',
                                        f'http://127.0.0.1:{server.server_port}/ordinary'], timeout=10)
        if body != b'backend-busybox-normal-response\n':
            raise RuntimeError('Ordinary local HTTP behavior differs')
    finally:
        server.shutdown()
        worker.join(timeout=5)
        server.server_close()
    print(json.dumps({'passed': True, 'binary_sha256': hashlib.sha256(binary.read_bytes()).hexdigest(),
                      'required_applets_present': True, 'normal_shell': True, 'normal_loopback_http': True,
                      'adversarial_inputs_executed': False, 'external_requests': 0}))


if __name__ == '__main__':
    main()
