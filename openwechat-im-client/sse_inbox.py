#!/usr/bin/env python3
"""
Push inbox script: connects to GET /stream and appends received messages to .data/inbox_pushed.md.
On disconnect, appends a disconnect record at the end of the file for the model to detect and inform the user.
Usage: run from the Skill root directory, or have the model invoke it after the user agrees to enable push.
Requires: requests (or urllib); .data/config.json must contain base_url and token.
"""
import json
import os
import sys
from datetime import datetime, timezone

# Script directory is the Skill root
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(SCRIPT_DIR, ".data")
CONFIG_PATH = os.path.join(DATA_DIR, "config.json")
INBOX_PUSHED_PATH = os.path.join(DATA_DIR, "inbox_pushed.md")
SEP = "─" * 40


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


def append_disconnect():
    ensure_data_dir()
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    with open(INBOX_PUSHED_PATH, "a", encoding="utf-8") as f:
        f.write("\n" + SEP + "\n[Disconnected] " + ts + "\n")


def main():
    cfg = load_config()
    if not cfg or not cfg.get("token") or not cfg.get("base_url"):
        print(
            ".data/config.json not found or missing base_url/token. "
            "Register first, save the token, then set base_url and token in config.json and run again."
        )
        sys.exit(1)

    base_url = cfg["base_url"].rstrip("/")
    token = cfg["token"]
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
    except Exception as e:
        print(f"Error reading stream: {e}", file=sys.stderr)
    finally:
        append_disconnect()
        print("SSE disconnected; disconnect record written to .data/inbox_pushed.md; the model can inform the user accordingly.")


if __name__ == "__main__":
    main()
