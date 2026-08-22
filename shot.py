"""Dev helper: render MainWindow states and save screenshots (no network)."""

import sys

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from main import MainWindow

out = sys.argv[1] if len(sys.argv) > 1 else "shot.png"
state = sys.argv[2] if len(sys.argv) > 2 else "empty"

app = QApplication(sys.argv)
w = MainWindow()
w.show()

META = {
    "status": 200,
    "reason": "OK",
    "headers": {
        "Content-Type": "application/json",
        "Cache-Control": "no-store",
        "Server": "nginx/1.25.0",
    },
    "content_type": "application/json",
    "binary": False,
    "size_kb": 1.2,
    "elapsed_ms": 143,
    "final_url": "https://api.example.com/users",
    "redirects": 0,
    "http_version": "HTTP/1.1",
    "request_method": "GET",
    "request_path": "/users",
    "request_headers": {"Host": "api.example.com", "User-Agent": "HTTP-Peek/1.0", "Accept": "*/*"},
}

if state == "success":
    w.url_input.setText("https://api.example.com/users")
    w.on_fetch_finished('{"id": 1, "name": "Ada", "tags": ["admin", "owner"], "active": true}', False, META)
elif state == "headers":
    w.url_input.setText("https://api.example.com/users")
    w.on_fetch_finished('{"id": 1, "name": "Ada"}', False, META)
    w.resp_tabs.setCurrentIndex(1)
elif state == "http_error":
    w.url_input.setText("https://api.example.com/missing")
    meta = dict(META, status=404, reason="Not Found", content_type="application/json")
    w.on_fetch_finished('{"detail": "User not found"}', False, meta)
elif state == "error":
    w.url_input.setText("https://api.example.com/down")
    w.on_fetch_finished("Could not connect to the server.", True, {})
elif state == "post":
    w._set_method("POST")
    w.url_input.setText("https://api.example.com/users")
    w.body_input.setPlainText('{"name": "Ada", "role": "admin"}')
elif state == "binary":
    w.url_input.setText("https://api.example.com/file.bin")
    meta = dict(META, content_type="application/x-trash", binary=True, size_kb=3.0)
    w.on_fetch_finished("\x89PNG\r\n\x1a\nRAW\x00\x01\x02content here", False, meta)
elif state == "menu":
    w._set_method("GET")
    w.url_input.setText("https://example.com")
    w.method_menu.exec(w.method_button.mapToGlobal(w.method_button.rect().bottomLeft()))


def grab():
    w.grab().save(out)
    app.quit()


if state == "menu":
    QTimer.singleShot(600, lambda: (w.method_menu.close(), w.grab().save(out), app.quit()))
else:
    QTimer.singleShot(400, grab)

app.exec()
