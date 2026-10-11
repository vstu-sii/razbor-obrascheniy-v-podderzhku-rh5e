"""LAB02 AI stub. It never suggests an answer without approved evidence."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json


def valid_request(data):
    required = {"request_id", "ticket_ref", "ticket_text", "history", "kb_version"}
    if not isinstance(data, dict) or set(data) != required:
        return False
    limits = {"request_id": 100, "ticket_ref": 100, "ticket_text": 10000, "kb_version": 100}
    if any(not isinstance(data[key], str) or not 1 <= len(data[key]) <= limit
           for key, limit in limits.items()):
        return False
    history = data["history"]
    return isinstance(history, list) and len(history) <= 50 and all(
        isinstance(item, dict) and set(item) == {"speaker", "text"}
        and item["speaker"] in ("customer", "operator")
        and isinstance(item["text"], str) and len(item["text"]) <= 10000
        for item in history
    )


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path != "/health":
            self.send_error(404)
            return
        self.respond(200, {"status": "ok", "service": "ai", "mode": "stub"})

    def do_POST(self):
        if self.path != "/v1/analyze":
            self.send_error(404)
            return
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if size > 65536:
                self.respond(413, {"error": "input_size"})
                return
            if size < 1:
                self.respond(400, {"error": "invalid_input"})
                return
            data = json.loads(self.rfile.read(size))
            if not valid_request(data):
                self.respond(400, {"error": "invalid_input"})
                return
        except (ValueError, UnicodeDecodeError):
            self.respond(400, {"error": "invalid_json"})
            return
        self.respond(200, {
            "request_id": data["request_id"], "status": "not_analyzed", "mode": "escalate",
            "category": "other", "urgency": "unassessed", "urgency_reason": "Заглушка не оценивает срочность",
            "summary": "Анализ пока не выполнен", "known_facts": [], "missing_data": [],
            "next_step": "Оператор разбирает обращение вручную", "evidence": [],
            "draft_reply": None, "needs_human_review": True, "warnings": ["AI service is a stub"],
        })

    def respond(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 8000), Handler).serve_forever()
