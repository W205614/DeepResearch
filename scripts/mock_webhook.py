"""Local-only webhook recorder used by Compose alert drills; never forwards messages."""
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DELIVERIES = []


class Recorder(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        try:
            body = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self.send_error(400)
            return
        DELIVERIES.append(body)
        self.send_response(204)
        self.end_headers()

    def do_GET(self):
        if self.path != "/deliveries":
            self.send_error(404)
            return
        body = json.dumps({"deliveries": DELIVERIES}, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        return


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 8090), Recorder).serve_forever()
