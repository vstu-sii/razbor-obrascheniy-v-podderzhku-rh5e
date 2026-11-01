"""LAB02 AI stub. It never suggests an answer without approved evidence."""
from http.server import BaseHTTPRequestHandler, HTTPServer
import json


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
            if size < 1 or size > 65536:
                self.respond(413, {"error": "input_size"})
                return
            data = json.loads(self.rfile.read(size))
            required = ("request_id", "ticket_ref", "ticket_text", "history", "kb_version")
            if (not isinstance(data, dict)
                    or any(key not in data for key in required)
                    or not all(isinstance(data[key], str) and data[key] for key in required if key != "history")
                    or not isinstance(data["history"], list)):
                self.respond(400, {"error": "invalid_input"})
                return
        except (ValueError, json.JSONDecodeError):
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
    HTTPServer(("0.0.0.0", 8000), Handler).serve_forever()
