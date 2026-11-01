"""Tiny OpenAI-compatible server: replies from a queue of (status, content) items."""

from __future__ import annotations

import json
import socketserver
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer


class _Server(HTTPServer):
    def server_bind(self):  # skip HTTPServer's getfqdn(): slow reverse DNS on some machines
        socketserver.TCPServer.server_bind(self)
        self.server_name, self.server_port = "localhost", self.server_address[1]


class FakeLLM:
    def __init__(self, replies: list[tuple[int, str]]):
        self.replies, self.requests = list(replies), []
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                outer.requests.append(body)
                status, content = outer.replies.pop(0) if len(outer.replies) > 1 else outer.replies[0]
                if status == 200:
                    payload = {"id": "x", "object": "chat.completion", "created": 0, "model": body["model"],
                               "choices": [{"index": 0, "finish_reason": "stop",
                                            "message": {"role": "assistant", "content": content}}],
                               "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20}}
                else:
                    payload = {"error": {"message": content, "type": "rate_limit"}}
                data = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                if status == 429:
                    self.send_header("retry-after", "0")
                self.end_headers()
                self.wfile.write(data)

        self.server = _Server(("127.0.0.1", 0), H)
        self.url = f"http://localhost:{self.server.server_port}/v1"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
