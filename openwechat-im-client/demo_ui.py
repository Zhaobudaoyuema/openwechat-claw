#!/usr/bin/env python3
"""
Minimal local UI for openwechat-im-client.

Reads files from .data/ (under this script directory) and serves a single-page
dashboard for:
- current target session
- basic chat status
- latest pushed messages
- pending SSE batch
"""

from __future__ import annotations

import html
import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
DATA_DIR = SCRIPT_DIR / ".data"

CONFIG_PATH = DATA_DIR / "config.json"
STATS_PATH = DATA_DIR / "stats.json"
CONTEXT_SNAPSHOT_PATH = DATA_DIR / "context_snapshot.json"
CONVERSATIONS_PATH = DATA_DIR / "conversations.md"
INBOX_PUSHED_PATH = DATA_DIR / "inbox_pushed.md"
BATCH_READY_PATH = DATA_DIR / "sse_batch_ready.md"


def read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def read_text(path: Path, max_chars: int = 6000) -> str:
    if not path.exists():
        return "(file not found)"
    text = path.read_text(encoding="utf-8", errors="replace")
    if len(text) > max_chars:
        return text[-max_chars:]
    return text


def render_page() -> str:
    config = read_json(CONFIG_PATH)
    stats = read_json(STATS_PATH)
    snapshot = read_json(CONTEXT_SNAPSHOT_PATH)

    target_session = snapshot.get("sse_target_session") or config.get("sse_target_session", "main")
    base_url = config.get("base_url", "")
    my_name = snapshot.get("my_name") or config.get("my_name", "")
    my_id = snapshot.get("my_id") or config.get("my_id", "")
    updated_at_utc = snapshot.get("updated_at_utc", "-")

    conversations = read_text(CONVERSATIONS_PATH)
    pushed = read_text(INBOX_PUSHED_PATH)
    pending = read_text(BATCH_READY_PATH)
    snapshot_raw = read_text(CONTEXT_SNAPSHOT_PATH, max_chars=3000)

    rows = []
    for key in [
        "messages_received",
        "messages_sent",
        "friends_count",
        "pending_incoming_count",
        "pending_outgoing_count",
        "last_sync_utc",
    ]:
        val = snapshot.get(key, stats.get(key, "-"))
        rows.append(f"<tr><td>{html.escape(key)}</td><td>{html.escape(str(val))}</td></tr>")

    return f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <meta http-equiv="refresh" content="3" />
  <title>OpenWechat Demo UI</title>
  <style>
    body {{ font-family: Arial, sans-serif; margin: 16px; background: #fafafa; }}
    h1 {{ margin: 0 0 10px 0; }}
    .muted {{ color: #666; font-size: 13px; }}
    .grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }}
    .card {{ background: #fff; border: 1px solid #ddd; border-radius: 8px; padding: 10px; }}
    pre {{ white-space: pre-wrap; word-break: break-word; background: #f6f8fa; padding: 8px; border-radius: 6px; max-height: 260px; overflow: auto; }}
    table {{ width: 100%; border-collapse: collapse; }}
    td {{ border-bottom: 1px solid #eee; padding: 4px 6px; font-size: 13px; }}
  </style>
</head>
<body>
  <h1>OpenWechat Minimal UI</h1>
  <div class="muted">Auto refresh every 3s. Data root: {html.escape(str(DATA_DIR))}</div>

  <div class="grid" style="margin-top: 12px;">
    <div class="card">
      <h3>Current Target Session</h3>
      <p><b>{html.escape(str(target_session))}</b></p>
      <div class="muted">base_url={html.escape(str(base_url))} | me=#{html.escape(str(my_id))} {html.escape(str(my_name))}</div>
      <div class="muted">snapshot updated_at_utc={html.escape(str(updated_at_utc))}</div>
    </div>
    <div class="card">
      <h3>Stats</h3>
      <table>{''.join(rows)}</table>
    </div>
  </div>

  <div class="grid" style="margin-top: 12px;">
    <div class="card">
      <h3>Latest Conversations Snapshot</h3>
      <pre>{html.escape(conversations)}</pre>
    </div>
    <div class="card">
      <h3>Latest Pushed Messages</h3>
      <pre>{html.escape(pushed)}</pre>
    </div>
  </div>

  <div class="card" style="margin-top: 12px;">
    <h3>Pending SSE Batch</h3>
    <pre>{html.escape(pending)}</pre>
  </div>

  <div class="card" style="margin-top: 12px;">
    <h3>Context Snapshot (Preferred)</h3>
    <pre>{html.escape(snapshot_raw)}</pre>
  </div>
</body>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path not in ("/", "/index.html"):
            self.send_error(404, "Not Found")
            return
        page = render_page().encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(page)))
        self.end_headers()
        self.wfile.write(page)


def main():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    host = "127.0.0.1"
    port = 8765
    server = HTTPServer((host, port), Handler)
    print(f"Demo UI running: http://{host}:{port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
