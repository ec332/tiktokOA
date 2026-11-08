"""The HTTP stand-in (C8, docs/status/standins/001): a SIMULATED receiver for a SUT's outbound
HTTP calls (webhooks, callbacks). It answers every request, keeps a journal of what it received,
and can be told to fail. Run from the runner image: `python -m platform_rt.standin [port]`.

- `GET /__journal`: the requests received, oldest first, sensitive headers masked;
- `POST /__fault {"status": int, "delay_s": float}`: answer later and/or with that status;
  `POST /__fault {}` clears it."""

import json
import re
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

SENSITIVE = re.compile(r"authorization|cookie|token|secret|key|signature|password", re.IGNORECASE)
MAX_BODY = 20_000  # characters kept per request

JOURNAL: list[dict] = []
FAULT: dict = {}
LOCK = threading.Lock()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def _reply(self, status: int, body) -> None:
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _any(self):
        raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        path = self.path.split("?")[0]
        if path == "/__journal" and self.command == "GET":
            with LOCK:
                return self._reply(200, list(JOURNAL))
        if path == "/__fault" and self.command == "POST":
            try:
                fault = json.loads(raw or b"{}")
            except ValueError:
                return self._reply(400, {"error": "a JSON object is required"})
            with LOCK:
                FAULT.clear()
                FAULT.update({k: fault[k] for k in ("status", "delay_s") if k in fault})
                return self._reply(200, dict(FAULT))
        headers = {k: "<masked>" if SENSITIVE.search(k) else v for k, v in self.headers.items()}
        with LOCK:  # journalled on receipt: a fault still shows the call was made
            JOURNAL.append({"n": len(JOURNAL), "at": time.time(), "method": self.command,
                            "path": self.path, "headers": headers,
                            "body": raw.decode(errors="replace")[:MAX_BODY]})  # fmt: skip
            fault = dict(FAULT)
        time.sleep(float(fault.get("delay_s") or 0))
        self._reply(int(fault.get("status") or 200), {"received": True})

    do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = _any


def serve(port: int = 8000) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    server.daemon_threads = True
    return server


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
    print(f"tp stand-in (SIMULATED) on :{port}", file=sys.stderr, flush=True)
    serve(port).serve_forever()
