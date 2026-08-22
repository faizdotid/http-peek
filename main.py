"""HTTP Peek — a tiny neobrutalist HTTP inspector built with PySide6."""

import json
import sys
import time
from pathlib import Path

import requests
from PySide6.QtCore import Qt, QThread, QTimer, Signal
from PySide6.QtGui import (
    QAction,
    QActionGroup,
    QColor,
    QFont,
    QFontDatabase,
    QGuiApplication,
    QPalette,
    QShortcut,
)
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QProgressBar,
    QPushButton,
    QStatusBar,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

APP_NAME = "HTTP Peek"
REQUEST_TIMEOUT = 10  # seconds
HTTP_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"]
BODY_METHODS = {"POST", "PUT", "PATCH"}
MAX_DISPLAY_CHARS = 500_000  # truncate huge bodies so the UI stays responsive

# Loopback hosts almost always serve plain HTTP in local development, so
# default to http:// for them (https:// would just fail with an SSL error).
LOCAL_HOSTS = {"localhost", "127.0.0.1", "0.0.0.0", "::1"}

# Content types whose body is safe to decode and show as text.
TEXTUAL_CONTENT_TYPES = (
    "text/",
    "application/json",
    "application/xml",
    "application/javascript",
    "application/x-www-form-urlencoded",
    "application/graphql",
    "image/svg",
)
HTTP_VERSIONS = {9: "HTTP/0.9", 10: "HTTP/1.0", 11: "HTTP/1.1", 20: "HTTP/2"}

SANS_CANDIDATES = (
    "Inter",
    "Lato",
    "Ubuntu Sans",
    "Ubuntu",
    "Segoe UI",
    "Noto Sans",
    "Cantarell",
    "DejaVu Sans",
)
MONO_CANDIDATES = (
    "JetBrains Mono",
    "MonoLisa",
    "Fira Code",
    "Cascadia Code",
    "Ubuntu Sans Mono",
    "Noto Sans Mono",
    "Consolas",
    "DejaVu Sans Mono",
)


def is_textual(content_type: str) -> bool:
    """Guess whether a Content-Type describes human-readable text."""
    if not content_type:
        return True
    ct = content_type.lower()
    return any(t in ct for t in TEXTUAL_CONTENT_TYPES) or "+json" in ct or "+xml" in ct


def normalize_url(url: str) -> str:
    """Add a scheme if the user omits it. Loopback hosts get http://,
    everything else https://."""
    url = url.strip()
    if url and "://" not in url:
        host = url.split("/", 1)[0]
        if host.startswith("[") and "]" in host:
            host = host[1 : host.index("]")]
        else:
            host = host.split(":", 1)[0]
        scheme = "http" if host.lower() in LOCAL_HOSTS else "https"
        url = f"{scheme}://{url}"
    return url


def pick_font(candidates, fallback):
    """Pick the first font family available on the system."""
    available = set(QFontDatabase.families())
    for family in candidates:
        if family in available:
            return family
    return fallback


def format_size(kb: float) -> str:
    return f"{kb / 1024:.1f} MB" if kb >= 1024 else f"{kb:.1f} KB"


def format_time(ms: int) -> str:
    return f"{ms / 1000:.2f} s" if ms >= 1000 else f"{ms} ms"


def display_body(text: str, meta: dict) -> str:
    """Prepare a response body for display: binary decoded as UTF-8,
    JSON pretty-print, and a truncation guard for very large payloads."""
    if meta.get("binary"):
        ct = meta.get("content_type") or "unknown type"
        if text:
            return (
                f"[Binary content: {ct}, {format_size(meta['size_kb'])} — decoded as UTF-8.]\n\n"
                + text
            )
        return f"[Binary content: {ct}, {format_size(meta['size_kb'])} — body not displayed.]"
    if text.strip()[:1] in ("{", "["):
        try:
            text = json.dumps(json.loads(text), indent=2, ensure_ascii=False)
        except ValueError:
            pass
    if len(text) > MAX_DISPLAY_CHARS:
        text = (
            text[:MAX_DISPLAY_CHARS]
            + f"\n\n… truncated — showing {MAX_DISPLAY_CHARS:,} of {len(text):,} characters."
        )
    return text


def format_headers(meta: dict) -> str:
    """Request + response headers in a curl-like `>` / `<` layout."""
    lines = [f"> {meta['request_method']} {meta['request_path']}"]
    lines += [f"> {k}: {v}" for k, v in meta["request_headers"].items()]
    lines.append("")
    status = " ".join(
        part
        for part in (meta["http_version"], str(meta["status"]), meta["reason"])
        if part
    )
    lines.append(f"< {status}")
    lines += [f"< {k}: {v}" for k, v in meta["headers"].items()]
    return "\n".join(lines)


def build_stylesheet(sans: str, mono: str, arrow: str, check: str) -> str:
    """Neobrutalist light theme: cream canvas, ink borders, yellow accents."""
    return f"""
        QMainWindow {{
            background-color: #FFF4D6;
        }}
        QWidget {{
            color: #111111;
            font-family: "{sans}";
            font-size: 13px;
        }}
        QLabel[role="title"] {{
            font-size: 26px;
            font-weight: 900;
            color: #111111;
        }}
        QLabel[role="subtitle"] {{
            font-size: 13px;
            font-weight: 600;
            color: #4a4a4a;
        }}
        QLabel[role="section"] {{
            background-color: #FFD60A;
            color: #111111;
            border: 2px solid #111111;
            border-radius: 6px;
            padding: 3px 10px;
            font-size: 11px;
            font-weight: 800;
        }}
        QFrame#card {{
            background-color: #ffffff;
            border: 2px solid #111111;
            border-radius: 12px;
        }}
        QPushButton[role="method"] {{
            background-color: #ffffff;
            color: #111111;
            border: 2px solid #111111;
            border-radius: 8px;
            padding: 9px 26px;
            min-height: 20px;
            font-weight: 700;
            text-align: center;
        }}
        QPushButton[role="method"]:hover {{
            background-color: #FFF0B3;
        }}
        QPushButton[role="method"]:pressed {{
            background-color: #FFE9A8;
        }}
        QPushButton[role="method"]::menu-indicator {{
            subcontrol-origin: padding;
            subcontrol-position: center right;
            right: 12px;
            image: url("{arrow}");
            width: 12px;
            height: 12px;
        }}
        QMenu {{
            background-color: #ffffff;
            color: #111111;
            border: 2px solid #111111;
            border-radius: 8px;
            padding: 6px;
        }}
        QMenu::item {{
            padding: 7px 22px 7px 10px;
            border-radius: 6px;
            font-weight: 700;
        }}
        QMenu::item:selected {{
            background-color: #FFD60A;
            color: #111111;
        }}
        QMenu::indicator {{
            width: 16px;
            height: 16px;
        }}
        QMenu::indicator:checked {{
            image: url("{check}");
        }}
        QMenu::indicator:unchecked {{
            image: none;
        }}
        QLineEdit {{
            background-color: #ffffff;
            color: #111111;
            border: 2px solid #111111;
            border-radius: 8px;
            padding: 9px 14px;
            min-height: 20px;
            selection-background-color: #FFD60A;
            selection-color: #111111;
        }}
        QLineEdit:hover {{
            background-color: #FFFCF0;
        }}
        QLineEdit:focus {{
            border: 2px solid #2F80ED;
        }}
        QTextEdit {{
            background-color: #ffffff;
            color: #111111;
            font-family: "{mono}";
            font-size: 13px;
            border: 2px solid #111111;
            border-radius: 8px;
            padding: 10px;
            selection-background-color: #FFD60A;
            selection-color: #111111;
        }}
        QTextEdit:focus {{
            border: 2px solid #2F80ED;
        }}
        QTextEdit[bare="true"] {{
            border: none;
            border-radius: 0;
            background-color: transparent;
            padding: 8px 10px;
        }}
        QTextEdit[bare="true"]:focus {{
            border: none;
        }}
        QTextEdit[error="true"] {{
            color: #9b1c1c;
            background-color: #FFE4E4;
        }}
        QTabWidget::pane {{
            background-color: #ffffff;
            border: 2px solid #111111;
            border-top-left-radius: 0;
            border-top-right-radius: 8px;
            border-bottom-left-radius: 8px;
            border-bottom-right-radius: 8px;
            top: -2px;
        }}
        QTabBar::tab {{
            background-color: #FFE9A8;
            color: #111111;
            border: 2px solid #111111;
            border-bottom: none;
            border-top-left-radius: 8px;
            border-top-right-radius: 8px;
            padding: 6px 16px;
            margin-right: 6px;
            font-weight: 700;
        }}
        QTabBar::tab:selected {{
            background-color: #FFD60A;
        }}
        QTabBar::tab:!selected:hover {{
            background-color: #FFF0B3;
        }}
        QPushButton {{
            background-color: #FFD60A;
            color: #111111;
            border: 2px solid #111111;
            border-radius: 8px;
            padding: 9px 20px;
            min-height: 20px;
            font-weight: 800;
        }}
        QPushButton:hover {{
            background-color: #FFC300;
        }}
        QPushButton:pressed {{
            background-color: #F5B800;
        }}
        QPushButton:disabled {{
            background-color: #E5E5E5;
            color: #9CA3AF;
            border: 2px solid #9CA3AF;
        }}
        QPushButton[role="secondary"] {{
            background-color: #ffffff;
            color: #111111;
            border: 2px solid #111111;
            padding: 8px 16px;
            font-weight: 700;
        }}
        QPushButton[role="secondary"]:hover {{
            background-color: #FFF0B3;
        }}
        QPushButton[role="secondary"]:pressed {{
            background-color: #FFE9A8;
        }}
        QPushButton[role="secondary"]:disabled {{
            background-color: #E5E5E5;
            color: #9CA3AF;
            border: 2px solid #9CA3AF;
        }}
        QPushButton[role="cancel"] {{
            background-color: #FECACA;
            color: #111111;
            border: 2px solid #111111;
            border-radius: 8px;
            padding: 9px 20px;
            min-height: 20px;
            font-weight: 800;
        }}
        QPushButton[role="cancel"]:hover {{
            background-color: #FCA5A5;
        }}
        QPushButton[role="cancel"]:pressed {{
            background-color: #F87171;
        }}
        QLabel[kind="status"], QLabel[kind="size"], QLabel[kind="time"] {{
            font-size: 11px;
            font-weight: 800;
            padding: 2px 10px;
            border-radius: 6px;
            border: 2px solid #111111;
            background-color: #ffffff;
            color: #111111;
        }}
        QLabel[kind="status"][status="success"] {{
            background-color: #A7F3D0;
        }}
        QLabel[kind="status"][status="info"] {{
            background-color: #BFDBFE;
        }}
        QLabel[kind="status"][status="warning"] {{
            background-color: #FDE68A;
        }}
        QLabel[kind="status"][status="error"] {{
            background-color: #FECACA;
        }}
        QStatusBar {{
            background-color: #FFF4D6;
            color: #111111;
            border-top: 2px solid #111111;
            font-size: 12px;
            font-weight: 600;
        }}
        QStatusBar::item {{
            border: none;
        }}
        QProgressBar {{
            background-color: #ffffff;
            border: 1.5px solid #111111;
            border-radius: 4px;
        }}
        QProgressBar::chunk {{
            background-color: #FFD60A;
            border-radius: 2px;
        }}
        QScrollBar:vertical {{
            background: transparent;
            width: 12px;
            margin: 4px 2px;
        }}
        QScrollBar::handle:vertical {{
            background: #111111;
            border-radius: 4px;
            min-height: 24px;
        }}
        QScrollBar::handle:vertical:hover {{
            background: #2F80ED;
        }}
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
            height: 0;
        }}
        QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
            background: transparent;
        }}
        QScrollBar:horizontal {{
            background: transparent;
            height: 12px;
            margin: 2px 4px;
        }}
        QScrollBar::handle:horizontal {{
            background: #111111;
            border-radius: 4px;
            min-width: 24px;
        }}
        QScrollBar::handle:horizontal:hover {{
            background: #2F80ED;
        }}
        QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
            width: 0;
        }}
        QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{
            background: transparent;
        }}
        QToolTip {{
            background-color: #FFD60A;
            color: #111111;
            border: 2px solid #111111;
            border-radius: 6px;
            padding: 6px 8px;
            font-family: "{sans}";
            font-size: 12px;
            font-weight: 600;
        }}
    """


class FetchWorker(QThread):
    """Worker thread so the UI doesn't freeze while making an HTTP request."""

    # (text, is_error, meta) — meta is {status, reason, headers, size_kb,
    # elapsed_ms, content_type, binary, final_url, redirects, http_version,
    # request_method, request_path, request_headers}
    done = Signal(str, bool, dict)

    def __init__(self, method, url, body=None, parent=None):
        super().__init__(parent)
        self.method = method
        self.url = url
        self.body = body

    def run(self):
        started = time.monotonic()
        headers = {"User-Agent": "HTTP-Peek/1.0"}
        kwargs = {
            "timeout": REQUEST_TIMEOUT,
            "headers": headers,
        }
        if self.body and self.method in BODY_METHODS:
            kwargs["data"] = self.body.encode("utf-8")
            try:
                # A JSON body is almost always meant to be sent as JSON.
                json.loads(self.body)
                headers["Content-Type"] = "application/json"
            except ValueError:
                # Looks like form data → tell the server how to parse it.
                if "&" in self.body and "=" in self.body:
                    headers["Content-Type"] = "application/x-www-form-urlencoded"

        try:
            resp = requests.request(self.method, self.url, **kwargs)
            elapsed_ms = int((time.monotonic() - started) * 1000)
            raw = resp.content
            content_type = resp.headers.get("Content-Type", "")
            # Explicit binary type OR a body that clearly isn't text (null
            # bytes) even when the server forgot the Content-Type header.
            binary = (content_type and not is_textual(content_type)) or (
                b"\x00" in raw[:2048]
            )
            meta = {
                "status": resp.status_code,
                "reason": resp.reason or "",
                "headers": dict(resp.headers),
                "content_type": content_type,
                "binary": binary,
                "size_kb": len(raw) / 1024,
                "elapsed_ms": elapsed_ms,
                "final_url": resp.url,
                "redirects": len(resp.history),
                "http_version": HTTP_VERSIONS.get(
                    getattr(resp.raw, "version", None), ""
                ),
                "request_method": resp.request.method,
                "request_path": resp.request.path_url,
                "request_headers": dict(resp.request.headers),
            }
            # NOTE: no raise_for_status() — an inspection tool must show the
            # body of 4xx/5xx responses too; that is what the user came for.
            text = raw.decode("utf-8", errors="replace") if binary else resp.text
            self.done.emit(text, False, meta)
        except requests.exceptions.MissingSchema:
            self.done.emit("Invalid URL — use a format like https://example.com", True, {})
        except requests.exceptions.Timeout:
            self.done.emit(f"Request timed out (>{REQUEST_TIMEOUT} seconds).", True, {})
        except requests.exceptions.SSLError as e:
            self.done.emit(f"SSL error: {e}", True, {})
        except requests.exceptions.ConnectionError:
            self.done.emit("Could not connect to the server.", True, {})
        except requests.exceptions.TooManyRedirects:
            self.done.emit("Too many redirects.", True, {})
        except requests.exceptions.RequestException as e:
            self.done.emit(str(e), True, {})


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.setMinimumSize(760, 560)
        self.resize(920, 680)
        self.worker = None
        self._busy = False
        self.current_method = "GET"

        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setContentsMargins(28, 24, 28, 18)
        layout.setSpacing(20)

        layout.addLayout(self._build_header())
        self.request_card = self._build_request_card()
        layout.addWidget(self.request_card)
        self.response_card = self._build_response_card()
        layout.addWidget(self.response_card, stretch=1)
        self._build_status_bar()

        # Hard offset shadows — the neobrutalism signature (no blur)
        for widget in (self.request_card, self.response_card):
            self._add_hard_shadow(widget, 6, 6)
        for widget in (
            self.method_button,
            self.url_input,
            self.body_input,
            self.resp_tabs,
            self.send_button,
            self.copy_button,
            self.clear_button,
        ):
            self._add_hard_shadow(widget, 4, 4)

        self.setCentralWidget(central)

        # Shortcuts: Ctrl+L focuses the URL, Ctrl+Enter sends
        QShortcut(Qt.CTRL | Qt.Key_L, self, activated=self._focus_url)
        QShortcut(Qt.CTRL | Qt.Key_Return, self, activated=self.start_fetch)
        QShortcut(Qt.CTRL | Qt.Key_Enter, self, activated=self.start_fetch)

        self._apply_style()
        self._update_response_actions()
        self.url_input.setFocus()

    # --- UI construction -------------------------------------------------

    def _build_header(self):
        header = QHBoxLayout()
        header.setSpacing(0)
        title_block = QVBoxLayout()
        title_block.setSpacing(2)
        self.title_label = QLabel("HTTP PEEK")
        self.title_label.setProperty("role", "title")
        subtitle = QLabel("Inspect HTTP responses — fast & simple")
        subtitle.setProperty("role", "subtitle")
        title_block.addWidget(self.title_label)
        title_block.addWidget(subtitle)
        header.addLayout(title_block)
        header.addStretch(1)
        return header

    def _build_request_card(self):
        card = QFrame()
        card.setObjectName("card")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(16, 14, 16, 14)
        card_layout.setSpacing(10)

        self.req_label = QLabel("REQUEST")
        self.req_label.setProperty("role", "section")
        self._add_hard_shadow(self.req_label, 3, 3)
        card_layout.addWidget(self.req_label, 0, Qt.AlignLeft)

        self._build_method_menu()
        req_row = QHBoxLayout()
        req_row.setSpacing(8)

        self.method_button = QPushButton("GET")
        self.method_button.setProperty("role", "method")
        self.method_button.setFixedWidth(118)
        self.method_button.setCursor(Qt.PointingHandCursor)
        self.method_button.setToolTip("HTTP method")
        self.method_button.setAccessibleName("HTTP method")
        self.method_button.setMenu(self.method_menu)
        req_row.addWidget(self.method_button)

        self.url_input = QLineEdit()
        self.url_input.setPlaceholderText("https://example.com")
        self.url_input.setClearButtonEnabled(True)
        self.url_input.setAccessibleName("Request URL")
        self.url_input.setToolTip("Enter a URL — Enter sends it, Ctrl+L focuses it")
        self.url_input.returnPressed.connect(self.start_fetch)
        req_row.addWidget(self.url_input, stretch=1)

        self.send_button = QPushButton("Send")
        self.send_button.setCursor(Qt.PointingHandCursor)
        self.send_button.setDefault(True)
        self.send_button.setToolTip("Send request (Enter / Ctrl+Enter)")
        self.send_button.setAccessibleName("Send request")
        self.send_button.clicked.connect(self.start_fetch)
        req_row.addWidget(self.send_button)
        card_layout.addLayout(req_row)

        self.body_label = QLabel("BODY")
        self.body_label.setProperty("role", "section")
        self._add_hard_shadow(self.body_label, 3, 3)
        self.body_input = QTextEdit()
        self.body_input.setPlaceholderText("Request body (JSON, form, text, ...)")
        self.body_input.setFixedHeight(88)
        self.body_input.setAcceptRichText(False)
        self.body_input.setAccessibleName("Request body")
        card_layout.addWidget(self.body_label, 0, Qt.AlignLeft)
        card_layout.addWidget(self.body_input)
        self.body_label.hide()
        self.body_input.hide()
        return card

    def _build_method_menu(self):
        self.method_menu = QMenu(self)
        self.method_menu.setWindowFlags(
            Qt.Popup | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint
        )
        self.method_menu.setAttribute(Qt.WA_TranslucentBackground)
        self._add_hard_shadow(self.method_menu, 5, 5)
        group = QActionGroup(self.method_menu)
        group.setExclusive(True)
        self.method_actions = {}
        for method in HTTP_METHODS:
            action = QAction(method, self.method_menu, checkable=True)
            action.setActionGroup(group)
            action.triggered.connect(lambda checked, m=method: self._set_method(m))
            self.method_actions[method] = action
            self.method_menu.addAction(action)
        self.method_actions["GET"].setChecked(True)

    def _build_response_card(self):
        card = QFrame()
        card.setObjectName("card")
        resp_layout = QVBoxLayout(card)
        resp_layout.setContentsMargins(16, 14, 16, 14)
        resp_layout.setSpacing(10)

        resp_header = QHBoxLayout()
        resp_header.setSpacing(8)
        self.resp_label = QLabel("RESPONSE")
        self.resp_label.setProperty("role", "section")
        self._add_hard_shadow(self.resp_label, 3, 3)
        resp_header.addWidget(self.resp_label)
        resp_header.addStretch(1)
        self.status_chip = self._make_chip("status", "HTTP status code")
        self.size_chip = self._make_chip("size", "Response body size")
        self.time_chip = self._make_chip("time", "Request duration")
        for chip in (self.status_chip, self.size_chip, self.time_chip):
            chip.hide()
            resp_header.addWidget(chip)
        resp_layout.addLayout(resp_header)

        self.resp_tabs = QTabWidget()
        self.resp_tabs.setObjectName("respTabs")

        self.response_body = QTextEdit()
        self.response_body.setProperty("bare", True)
        self.response_body.setReadOnly(True)
        self.response_body.setLineWrapMode(QTextEdit.WidgetWidth)
        self.response_body.setAcceptRichText(False)
        self.response_body.setPlaceholderText("Response body will appear here.")
        self.response_body.setAccessibleName("Response body")
        self.resp_tabs.addTab(self.response_body, "Body")

        self.response_headers = QTextEdit()
        self.response_headers.setProperty("bare", True)
        self.response_headers.setReadOnly(True)
        self.response_headers.setLineWrapMode(QTextEdit.WidgetWidth)
        self.response_headers.setAcceptRichText(False)
        self.response_headers.setPlaceholderText(
            "Request & response headers will appear here."
        )
        self.response_headers.setAccessibleName("Response headers")
        self.resp_tabs.addTab(self.response_headers, "Headers")

        resp_layout.addWidget(self.resp_tabs, stretch=1)

        actions = QHBoxLayout()
        actions.setSpacing(8)
        actions.addStretch(1)
        self.copy_button = QPushButton("Copy")
        self.copy_button.setProperty("role", "secondary")
        self.copy_button.setCursor(Qt.PointingHandCursor)
        self.copy_button.setToolTip("Copy current tab to clipboard")
        self.copy_button.clicked.connect(self.copy_response)
        actions.addWidget(self.copy_button)
        self.clear_button = QPushButton("Clear")
        self.clear_button.setProperty("role", "secondary")
        self.clear_button.setCursor(Qt.PointingHandCursor)
        self.clear_button.setToolTip("Clear response")
        self.clear_button.clicked.connect(self.clear_response)
        actions.addWidget(self.clear_button)
        resp_layout.addLayout(actions)
        return card

    def _build_status_bar(self):
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)  # indeterminate
        self.progress.setFixedWidth(120)
        self.progress.setFixedHeight(8)
        self.progress.setTextVisible(False)
        self.progress.hide()
        self.status_bar.addPermanentWidget(self.progress)
        self.status_bar.showMessage("Ready")

    # --- Small helpers ---------------------------------------------------

    def _make_chip(self, kind, tooltip):
        chip = QLabel()
        chip.setProperty("kind", kind)
        chip.setToolTip(tooltip)
        return chip

    @staticmethod
    def _add_hard_shadow(widget, dx=4, dy=4):
        """Solid offset shadow with no blur — the neobrutalism signature."""
        shadow = QGraphicsDropShadowEffect(widget)
        shadow.setBlurRadius(0)
        shadow.setOffset(dx, dy)
        shadow.setColor(QColor("#111111"))
        widget.setGraphicsEffect(shadow)

    @staticmethod
    def _track(widget, px):
        """Letter-spacing in pixels (stylesheets can't express it)."""
        font = widget.font()
        font.setLetterSpacing(QFont.AbsoluteSpacing, px)
        widget.setFont(font)

    def _focus_url(self):
        self.url_input.setFocus()
        self.url_input.selectAll()

    def _set_method(self, method):
        self.current_method = method
        self.method_button.setText(method)
        self._on_method_changed(method)

    def _on_method_changed(self, method):
        show = method in BODY_METHODS
        self.body_label.setVisible(show)
        self.body_input.setVisible(show)

    @staticmethod
    def _status_class(code):
        if 200 <= code < 300:
            return "success"
        if 300 <= code < 400:
            return "info"
        if 400 <= code < 500:
            return "warning"
        return "error"

    def _repolish(self, widget):
        widget.style().unpolish(widget)
        widget.style().polish(widget)

    def _apply_style(self):
        sans = pick_font(SANS_CANDIDATES, "Sans Serif")
        mono = pick_font(MONO_CANDIDATES, "Monospace")

        assets = Path(__file__).resolve().parent
        arrow = (assets / "down_arrow.svg").as_posix()
        check = (assets / "check.svg").as_posix()

        palette = self.palette()
        palette.setColor(QPalette.PlaceholderText, QColor("#6B7280"))
        self.setPalette(palette)

        # Apply at QApplication level so the QMenu popup (a separate top-level
        # widget) is styled correctly too.
        QApplication.instance().setStyleSheet(
            build_stylesheet(sans, mono, arrow, check)
        )

        # Letter-spacing for the display/label type — stylesheets can't express
        # it. Must run after the stylesheet, so widget.font() includes the QSS
        # family/size/weight.
        self._track(self.title_label, 1.0)
        for label in (self.req_label, self.resp_label):
            self._track(label, 0.6)

    # --- Request lifecycle ------------------------------------------------

    def start_fetch(self):
        if self._busy:
            self._cancel_fetch()
            return
        url = normalize_url(self.url_input.text())
        if not url:
            self._show_error("URL cannot be empty.")
            return
        self.url_input.setText(url)

        method = self.current_method
        body = self.body_input.toPlainText().strip()

        self._busy = True
        self.send_button.setText("Cancel")
        self.send_button.setToolTip("Cancel this request")
        self.send_button.setProperty("role", "cancel")
        self._repolish(self.send_button)
        self._set_error_state(False)
        self.response_body.clear()
        self.response_headers.clear()
        self.response_body.setPlaceholderText("Response body will appear here.")
        self._set_chips_visible(False)
        self._update_response_actions()
        self.progress.show()
        self.status_bar.showMessage(f"{method} {url} ...")

        self.worker = FetchWorker(method, url, body, self)
        self.worker.done.connect(self.on_fetch_finished)
        self.worker.finished.connect(self._cleanup_worker)
        self.worker.start()

    def _cancel_fetch(self):
        """Detach the running worker and free the UI immediately; its result
        (when it eventually arrives) is discarded."""
        worker = self.worker
        if worker is None:
            return
        self.worker = None
        self._busy = False
        try:
            worker.done.disconnect(self.on_fetch_finished)
            worker.finished.disconnect(self._cleanup_worker)
        except (RuntimeError, TypeError):
            pass
        worker.done.connect(lambda *_: None)
        worker.finished.connect(worker.deleteLater)
        self._set_send_idle()
        self.progress.hide()
        self.status_bar.showMessage("Request cancelled")
        self._update_response_actions()

    def _set_send_idle(self):
        self.send_button.setEnabled(True)
        self.send_button.setText("Send")
        self.send_button.setToolTip("Send request (Enter / Ctrl+Enter)")
        self.send_button.setProperty("role", "")
        self._repolish(self.send_button)

    def _cleanup_worker(self):
        worker = self.sender()
        if worker is self.worker:
            self.worker = None
        worker.deleteLater()

    def on_fetch_finished(self, text, is_error, meta):
        if self.sender() is not self.worker:
            return
        self._busy = False
        self._set_send_idle()
        self.progress.hide()
        if is_error:
            self._show_error(text)
            return
        self._set_error_state(False)
        if text or meta.get("binary"):
            self.response_body.setPlainText(display_body(text, meta))
        else:
            self.response_body.setPlaceholderText("(response body is empty)")
        self.response_headers.setPlainText(format_headers(meta))
        self._show_meta(meta)
        note = ""
        if meta["redirects"]:
            note = f" — {meta['redirects']} redirect(s) → {meta['final_url']}"
        self.status_bar.showMessage(f"Done — {meta['status']} {meta['reason']}{note}")
        self._update_response_actions()

    # --- Response display --------------------------------------------------

    def _show_meta(self, meta):
        code = meta["status"]
        self.status_chip.setText(f"{code} {meta['reason']}".strip())
        self.status_chip.setProperty("status", self._status_class(code))
        self.size_chip.setText(format_size(meta["size_kb"]))
        self.time_chip.setText(format_time(meta["elapsed_ms"]))
        for chip in (self.status_chip, self.size_chip, self.time_chip):
            self._repolish(chip)
            chip.show()

    def _set_chips_visible(self, visible):
        for chip in (self.status_chip, self.size_chip, self.time_chip):
            chip.setVisible(visible)

    def _set_error_state(self, is_error: bool):
        # Use a dynamic property so the stylesheet doesn't need string replacement
        self.response_body.setProperty("error", is_error)
        self._repolish(self.response_body)

    def _show_error(self, message):
        self._set_error_state(True)
        self.response_body.setPlainText(f"Error: {message}")
        self._set_chips_visible(False)
        self._update_response_actions()
        self.status_bar.showMessage(f"Error: {message}")

    def _update_response_actions(self):
        has_content = bool(
            self.response_body.toPlainText() or self.response_headers.toPlainText()
        )
        self.copy_button.setEnabled(has_content)
        self.clear_button.setEnabled(has_content)

    def copy_response(self):
        text = self.resp_tabs.currentWidget().toPlainText()
        if not text:
            self.status_bar.showMessage("Nothing to copy", 3000)
            return
        QGuiApplication.clipboard().setText(text)
        self.copy_button.setText("Copied ✓")
        QTimer.singleShot(1500, lambda: self.copy_button.setText("Copy"))
        self.status_bar.showMessage("Response copied to clipboard", 3000)

    def clear_response(self):
        self._set_error_state(False)
        self.response_body.clear()
        self.response_headers.clear()
        self.response_body.setPlaceholderText("Response body will appear here.")
        self._set_chips_visible(False)
        self._update_response_actions()
        self.status_bar.showMessage("Ready")

    def closeEvent(self, event):
        # Don't let a running worker outlive the window (QThread destroyed
        # while running = crash). Give it a grace period, then force-stop.
        worker = self.worker
        if worker is not None and worker.isRunning():
            try:
                worker.done.disconnect(self.on_fetch_finished)
                worker.finished.disconnect(self._cleanup_worker)
            except (RuntimeError, TypeError):
                pass
            worker.requestInterruption()
            if not worker.wait(3000):
                worker.terminate()
                worker.wait(1000)
            worker.deleteLater()
            self.worker = None
        super().closeEvent(event)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())
