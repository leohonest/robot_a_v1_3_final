#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""action_router HTTP 服务 (DGX :7793)

POST /action  {"query": "..."} -> {matched, action_id, target, params, result}
GET  /health  健康检查
"""
import os
import sys
import json
import logging

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.environ.get("DGX_SCRIPTS_DIR", "/data/linglong-project/scripts"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from action_router import route  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("action_router_server")


class RouterHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        logger.info(f"{self.command} {self.path} - {fmt % args if args else fmt}")

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")

    def do_OPTIONS(self):
        self.send_response(200)
        self._cors()
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        if self.path == "/health":
            body = json.dumps({"status": "ok", "service": "action_router", "port": 7793}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self._cors()
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        if self.path != "/action":
            self.send_response(404)
            self.end_headers()
            return
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length).decode("utf-8") if length else "{}"
        try:
            req = json.loads(raw)
        except json.JSONDecodeError:
            body = json.dumps({"status": "error", "msg": "invalid json"}).encode("utf-8")
            self.send_response(400)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self._cors()
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        result = route(req.get("query", ""))
        body = json.dumps(result, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self._cors()
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


if __name__ == "__main__":
    port = int(os.environ.get("ACTION_ROUTER_PORT", "7793"))
    server = ThreadingHTTPServer(("0.0.0.0", port), RouterHandler)
    print(f"Action Router Server on :{port}")
    server.serve_forever()
