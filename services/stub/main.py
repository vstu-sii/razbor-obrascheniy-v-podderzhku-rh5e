"""LAB02 boundary stub. Backend proxies analysis; other roles expose health only."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from urllib import request, error

SERVICE = os.environ.get("SERVICE_NAME", "stub")
MAX_BODY = 65536


class Handler(BaseHTTPRequestHandler):
    def respond(self, code, body):
        payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        if self.path == "/health":
            self.respond(200, {"status": "ok", "service": SERVICE, "mode": "stub"})
        else:
            self.respond(404, {"error": "not_found"})

    def do_POST(self):
        if SERVICE != "backend" or self.path != "/v1/analyze":
            self.respond(404, {"error": "not_found"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length > MAX_BODY:
                self.respond(413, {"error": "input_size"})
                return
            if length < 1:
                self.respond(400, {"error": "invalid_input"})
                return
            body = self.rfile.read(length)
            # Validate JSON before forwarding. The AI stub validates its structure.
            json.loads(body)
        except (ValueError, UnicodeDecodeError):
            self.respond(400, {"error": "invalid_json"})
            return
        target = os.environ["AI_URL"].rstrip("/") + "/v1/analyze"
        try:
            req = request.Request(target, data=body, headers={"Content-Type": "application/json"})
            with request.urlopen(req, timeout=3) as response:
                self.respond(response.status, json.loads(response.read()))
        except error.HTTPError as exc:
            try:
                payload = json.loads(exc.read())
            except (ValueError, UnicodeDecodeError):
                self.respond(503, {"error": "ai_unavailable", "next_step": "manual_review"})
            else:
                self.respond(exc.code, payload)
        except (error.URLError, TimeoutError, ValueError):
            self.respond(503, {"error": "ai_unavailable", "next_step": "manual_review"})


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 8000), Handler).serve_forever()
