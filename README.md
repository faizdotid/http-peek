# HTTP Peek

A simple GUI HTTP client to send requests and inspect responses, built with PySide6.

## Features

- Send requests with GET, POST, PUT, PATCH, DELETE, HEAD or OPTIONS
- Request body editor for POST/PUT/PATCH with automatic Content-Type detection (JSON / form)
- Response body viewer with automatic JSON pretty-printing and UTF-8 fallback for binary content
- Request & response headers in a curl-like `>` / `<` layout
- Status, size and duration chips
- Redirect information, timeouts and friendly error messages
- Cancel a running request

## Requirements

- Python 3.9+
- PySide6
- requests

## Run

```bash
python -m venv .venv
source .venv/bin/activate
pip install PySide6 requests
python main.py
```

## Usage

- Type a URL and press Enter (or Ctrl+Enter) to send
- Missing scheme defaults to `https://` (`http://` for localhost and loopback IPs)
- Ctrl+L focuses the URL field
- Pick the method from the dropdown — a Body field appears for POST/PUT/PATCH

## Test

```bash
python test_http_peek.py
```
