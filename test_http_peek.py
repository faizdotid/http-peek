"""Functional tests: pure helpers + FetchWorker against a local HTTP server."""

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from PySide6.QtWidgets import QApplication

import main
from main import FetchWorker, MainWindow, display_body, format_headers, format_size, format_time, is_textual, normalize_url

app = QApplication([])


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body=b"", ctype="text/plain", headers=None):
        self.send_response(code)
        if ctype:
            self.send_header("Content-Type", ctype)
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if body:
            self.wfile.write(body)

    def do_GET(self):
        if self.path == "/json":
            self._send(200, b'{"id":1,"name":"Ada"}', "application/json")
        elif self.path == "/redirect":
            self._send(302, headers={"Location": "/json"})
        elif self.path == "/binary":
            self._send(200, b"\x89PNG\r\n\x1a\n" + b"\x00" * 64, "image/png")
        elif self.path == "/noct":
            # Binary body but no Content-Type header at all — still binary.
            self._send(200, b"\x89PNG\r\n\x1a\n\x00\x00", None)
        elif self.path == "/empty":
            self._send(204)
        elif self.path == "/slow":
            time.sleep(1.5)
            self._send(200, b"slow", "text/plain")
        else:
            self._send(404, b"<html>nope</html>", "text/html")

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        ct = self.headers.get("Content-Type", "-")
        self._send(200, json.dumps({"got": body.decode(), "ct": ct}).encode(), "application/json")

    def log_message(self, *args):
        pass


server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
server.daemon_threads = True
threading.Thread(target=server.serve_forever, daemon=True).start()
BASE = f"http://127.0.0.1:{server.server_port}"


def fetch(method, url, body=None):
    """Run a worker synchronously and capture the emitted payload."""
    result = {}
    w = FetchWorker(method, url, body)
    w.done.connect(lambda text, err, meta: result.update(text=text, err=err, meta=meta))
    w.run()
    return result


# --- Pure helpers ---
assert normalize_url(" example.com ") == "https://example.com"
assert normalize_url("http://x.com") == "http://x.com"
assert normalize_url("") == ""
assert normalize_url("localhost:8000") == "http://localhost:8000"
assert normalize_url("127.0.0.1:5000/x") == "http://127.0.0.1:5000/x"
assert normalize_url("[::1]:8080") == "http://[::1]:8080"
assert is_textual("application/hal+json") and is_textual("") and not is_textual("image/png")
assert format_size(1.5) == "1.5 KB" and format_size(2048) == "2.0 MB"
assert format_time(143) == "143 ms" and format_time(1234) == "1.23 s"
assert MainWindow._status_class(200) == "success"
assert MainWindow._status_class(302) == "info"
assert MainWindow._status_class(404) == "warning"
assert MainWindow._status_class(500) == "error"

pretty = display_body('{"a":1}', {"content_type": "application/json"})
assert pretty == '{\n  "a": 1\n}', pretty
binary_note = display_body("", {"binary": True, "content_type": "image/png", "size_kb": 2.0})
assert "Binary content" in binary_note and "image/png" in binary_note
big = display_body("x" * (main.MAX_DISPLAY_CHARS + 10), {})
assert "truncated" in big and len(big) < main.MAX_DISPLAY_CHARS + 200

# --- Worker against the local server ---
r = fetch("GET", f"{BASE}/json")
assert not r["err"] and r["meta"]["status"] == 200
assert r["meta"]["content_type"] == "application/json"
assert display_body(r["text"], r["meta"]) == '{\n  "id": 1,\n  "name": "Ada"\n}'
assert r["meta"]["request_headers"]["User-Agent"] == "HTTP-Peek/1.0"
hdrs = format_headers(r["meta"])
assert hdrs.startswith("> GET /json")
assert "< HTTP/1.0 200" in hdrs or "< 200" in hdrs

# 404 must surface the body + meta (this was the raise_for_status bug)
r = fetch("GET", f"{BASE}/missing")
assert not r["err"], "4xx should not be a transport error"
assert r["meta"]["status"] == 404 and "nope" in r["text"]

r = fetch("GET", f"{BASE}/redirect")
assert r["meta"]["redirects"] == 1 and r["meta"]["final_url"].endswith("/json")

r = fetch("GET", f"{BASE}/binary")
assert r["meta"]["binary"]
assert "PNG" in r["text"], r["text"]
assert "Binary content" in display_body(r["text"], r["meta"])

# No Content-Type + null bytes → must still be treated as binary
r = fetch("GET", f"{BASE}/noct")
assert r["meta"]["binary"], r["meta"]
assert "Binary content" in display_body(r["text"], r["meta"])

r = fetch("GET", f"{BASE}/empty")
assert r["meta"]["status"] == 204 and r["text"] == ""

# JSON body → Content-Type guessed automatically
r = fetch("POST", f"{BASE}/echo", '{"x": 2}')
assert json.loads(r["text"])["ct"] == "application/json"
# Form-looking body → form Content-Type guessed automatically
r = fetch("POST", f"{BASE}/echo", "a=1&b=2")
assert json.loads(r["text"])["ct"] == "application/x-www-form-urlencoded"
r = fetch("POST", f"{BASE}/echo", "not json")
assert json.loads(r["text"])["ct"] == "-"

r = fetch("GET", "http://127.0.0.1:1/unreachable")
assert r["err"] and "connect" in r["text"].lower()

# --- UI: busy guard blocks a second request while one is running ---
w = MainWindow()
w.url_input.setText(f"{BASE}/json")
w._busy = True
w.start_fetch()  # must be ignored
assert w.worker is None
w._busy = False
w.start_fetch()
assert w.worker is not None
w.worker.wait(5000)
for _ in range(100):  # pump queued signals from the worker thread
    app.processEvents()
    if not w.status_chip.isHidden():
        break
    app.sendPostedEvents()
    time.sleep(0.02)
assert not w.status_chip.isHidden() and w.copy_button.isEnabled()
assert w.status_chip.text().startswith("200")
w.clear_response()
assert not w.copy_button.isEnabled() and w.status_chip.isHidden()

# --- UI: cancel detaches the worker and frees the UI immediately ---
w.url_input.setText(f"{BASE}/slow")
w.start_fetch()
assert w._busy and w.send_button.text() == "Cancel"
w._cancel_fetch()
assert not w._busy and w.worker is None
assert w.send_button.text() == "Send"
time.sleep(2.0)  # let the detached worker finish in the background

server.shutdown()
print("ALL TESTS PASSED")
