#!/usr/bin/env python3
"""
Push inbox script: connects to GET /stream and appends received messages to .data/inbox_pushed.md.
When accumulated messages reach batch_size, writes a batch to .data/sse_batch_ready.md for OpenClaw:
the model decides which session to send to (sessions_send) and sends once per batch.
On disconnect, appends a disconnect record and flushes any remaining buffered messages to sse_batch_ready.md.
Usage: run from the Skill root directory, or have the model invoke it after the user agrees to enable push.
Pass --target-session SESSION_KEY so the script knows (and persists) where to notify; the batch file will include it.
Requires: requests (or urllib); .data/config.json must contain base_url and token; optional batch_size (default 5).
"""
import argparse
import json
import os
import sys
from datetime import datetime, timezone

# Script directory is the Skill root
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(SCRIPT_DIR, ".data")
CONFIG_PATH = os.path.join(DATA_DIR, "config.json")
INBOX_PUSHED_PATH = os.path.join(DATA_DIR, "inbox_pushed.md")
BATCH_READY_PATH = os.path.join(DATA_DIR, "sse_batch_ready.md")
SEP = "─" * 40
DEFAULT_BATCH_SIZE = 5


def load_config():
    if not os.path.isfile(CONFIG_PATH):
        return None
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def ensure_data_dir():
    os.makedirs(DATA_DIR, exist_ok=True)


def append_message(payload: str):
    ensure_data_dir()
    need_sep = os.path.exists(INBOX_PUSHED_PATH) and os.path.getsize(INBOX_PUSHED_PATH) > 0
    with open(INBOX_PUSHED_PATH, "a", encoding="utf-8") as f:
        if need_sep:
            f.write("\n" + SEP + "\n")
        f.write(payload.strip())
        f.write("\n")


def write_batch_ready(messages: list[str], target_session: str | None = None):
    """Write accumulated messages to sse_batch_ready.md; include target_session if set (from --target-session)."""
    if not messages:
        return
    ensure_data_dir()
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    with open(BATCH_READY_PATH, "w", encoding="utf-8") as f:
        f.write("# SSE 待发批次 (" + str(len(messages)) + " 条)\n")
        f.write("> " + ts + "\n")
        if target_session:
            f.write("> Target session: " + target_session + "\n")
        f.write("\n---\n\n")
        for i, payload in enumerate(messages):
            if i:
                f.write("\n" + SEP + "\n\n")
            f.write(payload.strip())
            f.write("\n")
        f.write("\n")


def append_disconnect():
    ensure_data_dir()
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    with open(INBOX_PUSHED_PATH, "a", encoding="utf-8") as f:
        f.write("\n" + SEP + "\n[Disconnected] " + ts + "\n")


def main():
    parser = argparse.ArgumentParser(
        description="Connect to GET /stream; append messages to inbox_pushed.md; write batches to sse_batch_ready.md."
    )
    parser.add_argument(
        "--target-session",
        metavar="SESSION_KEY",
        help="OpenClaw session key to notify for each batch (e.g. main). Saved to .data/config.json as sse_target_session and written into each batch file.",
    )
    args = parser.parse_args()

    cfg = load_config()
    if not cfg or not cfg.get("token") or not cfg.get("base_url"):
        print(
            ".data/config.json not found or missing base_url/token. "
            "Register first, save the token, then set base_url and token in config.json and run again."
        )
        sys.exit(1)

    # Persist target session from CLI so batch file and later processing know where to send
    target_session: str | None = args.target_session or cfg.get("sse_target_session")
    if args.target_session:
        ensure_data_dir()
        cfg["sse_target_session"] = args.target_session
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)

    base_url = cfg["base_url"].rstrip("/")
    token = cfg["token"]
    batch_size = int(cfg.get("batch_size", DEFAULT_BATCH_SIZE))
    if batch_size < 1:
        batch_size = 1
    stream_url = base_url + "/stream"

    try:
        import requests
    except ImportError:
        print("requests is required: pip install requests")
        sys.exit(1)

    headers = {"X-Token": token, "Accept": "text/event-stream"}
    try:
        r = requests.get(stream_url, headers=headers, stream=True, timeout=60)
        r.raise_for_status()
    except requests.exceptions.HTTPError as e:
        if e.response.status_code == 429:
            print("Error: SSE connection limit reached for this IP (max 1).")
        elif e.response.status_code == 401:
            print("Error: Invalid token.")
        else:
            print(f"Connection failed: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"Connection failed: {e}")
        sys.exit(1)

    # Buffer for batch: when length >= batch_size, write sse_batch_ready.md (with target_session if set)
    message_buffer: list[str] = []
    try:
        buf = []
        for line in r.iter_lines(decode_unicode=True):
            if line is None:
                continue
            if line.startswith("data:"):
                buf.append(line[5:].lstrip())
            elif line == "" and buf:
                full = "\n".join(buf)
                buf = []
                if full.strip() and not full.strip().startswith(": ping"):
                    append_message(full)
                    message_buffer.append(full)
                    if len(message_buffer) >= batch_size:
                        write_batch_ready(message_buffer, target_session)
                        message_buffer = []
    except Exception as e:
        print(f"Error reading stream: {e}", file=sys.stderr)
    finally:
        if message_buffer:
            write_batch_ready(message_buffer, target_session)
        append_disconnect()
        print(
            "SSE disconnected; disconnect record written to .data/inbox_pushed.md. "
            "Any buffered batch written to .data/sse_batch_ready.md for OpenClaw session delivery."
        )


if __name__ == "__main__":
    main()
