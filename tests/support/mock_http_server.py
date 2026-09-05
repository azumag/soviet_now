#!/usr/bin/env python3
"""tests/support/mock_http_server.py - docich#39

sentinel を使った回帰テスト用の最小 mock HTTP サーバ。
受け取った method/path/headers/body を JSON で1リクエスト1行、
LOG_FILE (第2引数) に追記する。応答を DELAY_SEC (第3引数, 既定0) 秒
遅延させることで、呼び出し元 curl プロセスが生きている間に
`ps`/`/proc` から argv・environment を観測できるようにする。

引数: PORT LOG_FILE [DELAY_SEC]
"""
import http.server
import json
import sys
import time


class Handler(http.server.BaseHTTPRequestHandler):
    def _handle(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length).decode("utf-8", "replace") if length else ""
        record = {
            "method": self.command,
            "path": self.path,
            "headers": {k: v for k, v in self.headers.items()},
            "body": body,
        }
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        if DELAY_SEC > 0:
            time.sleep(DELAY_SEC)
        payload = json.dumps({"ok": True}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    do_GET = _handle
    do_POST = _handle

    def log_message(self, fmt, *args):  # silence default stderr logging
        pass


if __name__ == "__main__":
    port = int(sys.argv[1])
    LOG_FILE = sys.argv[2]
    DELAY_SEC = float(sys.argv[3]) if len(sys.argv) > 3 else 0.0
    server = http.server.HTTPServer(("127.0.0.1", port), Handler)
    server.serve_forever()
