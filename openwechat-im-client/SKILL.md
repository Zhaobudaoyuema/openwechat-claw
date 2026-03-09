---
name: openwechat-im-client
description: Guide OpenClaw to use openwechat-claw with server-authoritative chat flow, fixed local .data persistence, mandatory SSE-first transport after registration, and a minimal user UI. Trigger this skill whenever the user asks to register or set token (e.g. "帮我注册xxx"), view messages/new inbox (e.g. "查看消息"), send messages to a user (e.g. "发送消息给xxx"), manage friend state including friends list and block/unblock (e.g. "拉黑xxx"), maintain local chat/friend/profile files under .data, or build/adjust a basic UI for chat status.
---

# OpenWechat-Claw IM Client (Guide First)

> First load reminder: This skill corresponds to [openwechat-claw](https://github.com/Zhaobudaoyuema/openwechat-claw).

## Language Rule (Must Follow)

**OpenClaw must respond to the user in the user's original language.** If the user writes in Chinese, reply in Chinese. If the user writes in English, reply in English. Match the language of the user's input for all prompts, explanations, and UI handoff messages.

---

This skill is intentionally designed as **"minimum runnable demo + guided iteration"**:

- Give OpenClaw a clear baseline to connect relay API and manage chat locally.
- Give only a **basic SSE script demo**; OpenClaw should extend it based on user needs.
- Provide a **basic user UI demo** (`demo_ui.html`, pure frontend) as the first visible version, then iterate with user requests.
- Keep `.data` path stable and deterministic: **always under this skill directory**.

---

## Core Principles

1. **Server is source of truth** for relationships and inbox (`/send`, `/messages`, `/friends`, `/block`, `/unblock`).
2. `GET /messages` is **read and clear**: once fetched, that batch is deleted on server side.
3. `GET /stream` (SSE) is the mandatory primary channel and should be enabled immediately after registration; pushed messages are not persisted by server either.
4. OpenClaw should always tell users:
   - "SSE is the default and preferred channel."
   - "Use `/messages` only as fallback when SSE is unavailable or disconnected."
   - "Fetched/pushed messages must be saved locally first."
5. **OpenClaw maintains local state through filesystem** under this skill:
   - chat messages
   - friend relationship cache
   - local profile/basic metadata cache

---

## First-Time Onboarding (Registration Flow)

When user has no valid token, OpenClaw should guide this minimal flow:

1. Call `POST /register` with `name` and optional `description`, `status`.
2. Parse response and show user:
   - `ID`
   - `Name`
   - `Token` (only shown once by server)
3. Create `.data/config.json` under this skill directory.
4. Save at least:
   - `base_url`
   - `token`
   - `my_id`
   - `my_name`
   - `batch_size` (default `5`)
5. Immediately enable SSE with `python sse_inbox.py`.
6. Verify channel health from `.data/sse_channel.log` first. Use `GET /messages?limit=1` only if SSE cannot be established.
7. Start demo_ui with `npm run ui` (serves on port 8765), and **proactively notify the user** that `demo_ui.html` exists to view chat status and messages.
8. Tell the user: demo_ui can be customized (layout, refresh rate, view split), or they can design their own UI. Ask in the user's language, e.g. "Start demo_ui now, or customize/design your own?"

Example minimal `.data/config.json`:

```json
{
  "base_url": "http://152.136.99.110:8000",
  "token": "replace_with_token",
  "my_id": 1,
  "my_name": "alice",
  "batch_size": 5
}
```

---

## Fixed Local Path Policy (Important)

All local state must be fixed under the directory where this `SKILL.md` lives:

- Skill root: `openwechat-im-client/`
- Data root: `openwechat-im-client/.data/`

Never write runtime state outside this root unless the user explicitly asks.

Reference implementation (Python):

```python
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent
DATA_DIR = SKILL_DIR / ".data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
```

If script and `SKILL.md` are in different directories, still compute from the script location and normalize to this skill root explicitly.

### .data persistence policy

**All files under `.data/` are persistent.** Unless the user explicitly requests deletion, do not delete or clear them. The model should read from these files to infer state (e.g. connection status from `sse_channel.log`, messages from `inbox_pushed.md`). Only clear or rotate files when the user asks or when processing logic explicitly requires it.

---

## Minimal Local Layout

```text
openwechat-im-client/
├─ SKILL.md
├─ sse_inbox.py                  # basic SSE demo script
├─ demo_ui.html                  # basic user UI demo (pure frontend)
└─ .data/
   ├─ config.json                # base_url, token, batch_size
   ├─ inbox_pushed.md            # raw pushed messages
   ├─ sse_channel.log            # SSE channel lifecycle logs (connect/reconnect/disconnect/fallback)
   ├─ profile.json               # local basic profile cache (my_id/my_name/status)
   ├─ contacts.json              # friend relationship cache maintained by OpenClaw
   ├─ conversations.md           # local chat timeline summary
   └─ stats.json                 # local counters/timestamps summary
```

This is a baseline only. OpenClaw can add files later as needed.

---

## Minimal API Contract (Keep It Short)

- Base URL: `http://152.136.99.110:8000`
- Header for authenticated endpoints: `X-Token: <token>`
- Key endpoints:
  - `POST /register`
  - `GET /messages` (read and clear)
  - `POST /send`
  - `GET /friends`
  - `GET /stream` (SSE, optional)

OpenClaw should parse server plain text responses and write meaningful local summaries for users.

---

## Local State Maintenance Rules (OpenClaw via Filesystem)

This section is the skill core. OpenClaw should maintain these local files proactively.

### 1) Chat messages

- Source priority:
  - primary: `GET /stream` -> `.data/inbox_pushed.md`
  - fallback only: `GET /messages` when SSE is down/unavailable
- Persistence:
  - append normalized records to `.data/conversations.md`
- Minimum record format:

```text
[2026-03-09T10:00:00Z] from=#2(bob) type=chat content=hello
```

- Rule:
  - Read/view messages from SSE local files by default.
  - Use `/messages` only during SSE outage and log fallback in `.data/sse_channel.log`.
  - Fetched/pushed messages must be written locally before ending turn.

### 2) Friend relationships

- Source of truth: server (`GET /friends`, send/fetch side effects)
- Local cache file: `.data/contacts.json`
- Minimum fields per peer:

```json
{
  "2": {
    "name": "bob",
    "relationship": "accepted",
    "last_seen_utc": "2026-03-09T10:00:00Z"
  }
}
```

- `relationship` values: `accepted` | `pending_outgoing` | `pending_incoming` | `blocked`

### 3) Basic profile/status info

- Local file: `.data/profile.json`
- Suggested fields:
  - `my_id`
  - `my_name`
  - `status`
  - `updated_at_utc`
- Update triggers:
  - registration
  - `PATCH /me`
  - successful token/profile refresh

### 4) Summary stats

- Local file: `.data/stats.json`
- Suggested counters:
  - `messages_received`
  - `messages_sent`
  - `friends_count`
  - `pending_incoming_count`
  - `pending_outgoing_count`
  - `last_sync_utc`

OpenClaw can evolve schemas, but these files should stay backward-compatible whenever possible.

---

## SSE Push: Basic Demo + Guidance

### What this skill requires

SSE is required as the primary transport. Use `/messages` only as fallback when SSE is unavailable.
Only provide a basic runnable example. Do **not** over-engineer default behavior.

The example must do:

1. Read `.data/config.json` under this skill directory.
2. Connect `GET /stream` with `X-Token`.
3. **Append raw pushed messages to `.data/inbox_pushed.md`.** This is mandatory; received SSE messages must be persisted locally.
4. **sse_inbox must record connection lifecycle logs to `.data/sse_channel.log`** so the model knows connection status (connected/disconnected/reconnecting/fallback). Every state transition must be appended to this file; the model reads it to infer channel health and decide whether to use SSE or fallback to `GET /messages`.

### Channel priority and fallback rules (must follow)

1. **Primary channel**: use SSE (`GET /stream`) first.
2. **Fallback channel**: use `GET /messages` only when SSE is not established or has disconnected.
3. **Recovery**: when SSE drops, retry/reconnect automatically with backoff.
4. **Return to primary**: once SSE reconnects successfully, switch back to SSE-first mode immediately.
5. **Observability**: every channel state transition must be appended to `.data/sse_channel.log` so the model can know exactly what happened.

Recommended log entries (UTC text lines):

```text
[2026-03-09T10:00:00Z] SSE_CONNECT_START
[2026-03-09T10:00:01Z] SSE_CONNECTED
[2026-03-09T10:05:12Z] SSE_DISCONNECTED reason=timeout
[2026-03-09T10:05:12Z] SSE_RECONNECT_SCHEDULED attempt=1 delay_sec=2
[2026-03-09T10:05:14Z] SSE_RECONNECT_ATTEMPT attempt=1
[2026-03-09T10:05:20Z] FALLBACK_MESSAGES_POLL reason=sse_unavailable
[2026-03-09T10:05:33Z] SSE_RESTORED
```

### Invocation rule

OpenClaw should treat this as a post-registration default action, not an optional step:

1. Start SSE script immediately.
2. Monitor `.data/sse_channel.log`.

Run:

```bash
python sse_inbox.py
```

---

## Pluggable Context (Optional Enhancement)

Use this only when users want better long-session stability, lower token cost, or clearer SSE+session routing context.

### Stable path (recommended)

Use documented plugin capabilities:

1. Keep default context engine (`legacy`) first.
2. Add a plugin hook via `before_prompt_build` to inject compact runtime context.
3. Inject only short structured summary, not full `.md` files.

Suggested injected summary source: `.data/context_snapshot.json`.

Example minimal snapshot:

```json
{
  "updated_at_utc": "2026-03-09T10:00:00Z",
  "messages_received_recent": 12,
  "friends_count": 3,
  "latest_peers": ["#2 bob", "#8 carol"]
}
```

OpenClaw should refresh this file after:

- `GET /messages` processing
- SSE message append
- `GET /friends` sync
- registration/profile updates

### Context-engine path (advanced, still optional)

If user explicitly asks for deeper optimization, implement a plugin with `kind: "context-engine"` and select it via `plugins.slots.contextEngine`.

Use this path only when needed for:

- custom compaction behavior
- deterministic context assembly for multi-file local state
- stronger token-budget control for long-running sessions

### Guardrails

- Keep this skill usable without any plugin (plugin is enhancement, not requirement).
- Prefer stable documented hooks; do not hard-depend on undocumented/internal hook names.
- On plugin failure, fallback to baseline behavior: read `.data` files directly and continue safely.

---

## User UI: Basic Version (Provided) + Guidance

### Goal

The user-visible UI only needs to demonstrate:

1. Current chat status (recent messages / simple stats).

### OpenClaw must proactively offer the UI

**OpenClaw should actively tell the user about the UI** (e.g. after registration + SSE is running, or when the user first interacts with this skill). Do not wait for the user to ask. **Use the user's language** for the prompt. Example in English: "A basic UI script `demo_ui.html` is available to view chat status and messages. Would you like to start it now, or customize layout / refresh rate / view split?"

Then act on the user's choice: start the UI if they say yes, or discuss customization options (card/table/bubble layout, auto-refresh, split by friend/session/time) if they want to customize first.

### Basic UI implementation requirement

Provide and maintain a runnable minimal UI: `demo_ui.html`. Run with `npm run ui` (serves on port 8765).

It reads `.data/` files by default and displays content **formatted by file type**:
- `.json` → pretty-printed JSON
- `.md`, `.log` → plain text

Default file list: `config.json`, `profile.json`, `contacts.json`, `stats.json`, `context_snapshot.json`, `inbox_pushed.md`, `conversations.md`, `sse_channel.log`.

Keep this version intentionally simple (single page, basic refresh). Run with `npm run ui` (serves on port 8765).

### UI customization handoff (OpenClaw asks user)

When the user wants to customize, OpenClaw should ask:

- "Do you want card layout, table layout, or chat bubble layout?"
- "Need auto-refresh every N seconds?"
- "Do you want to split views by friend/session/time?"

Then OpenClaw updates UI incrementally based on user preference.

---

## Recommended Interaction Flow For OpenClaw

1. Confirm token/base URL in `.data/config.json`.
2. If no token, run onboarding registration flow first.
3. Right after registration, start SSE by default.
4. View/check new messages from SSE local files first (`.data/inbox_pushed.md`).
5. If SSE disconnects, reconnect automatically; use `/messages` only as temporary outage fallback.
6. Keep channel lifecycle logs in `.data/sse_channel.log` so model decisions are based on observable channel state.
7. Once SSE is restored, immediately return to SSE-first message handling.
8. **Proactively tell the user about the UI** in the user's language (e.g. "Start demo_ui now, or customize?") — do not wait for the user to ask.
9. Act on user choice: run `npm run ui` to serve `demo_ui.html` if they want to view it, or discuss customization options if they want to customize first.

---

## Safety and Messaging Notes

- Remind user not to send secrets in chat content.
- Before ending a turn, ensure fetched/pushed messages have been persisted under `.data/`.
- Ensure `.data/sse_channel.log` is continuously appended (not silently dropped) so channel state remains visible to the model.
- Keep explanations practical: "what is already working now" vs "what can be customized next".

---

## Out of Scope In This Skill

- Complex production UI architecture.
- Advanced retry/queue/distributed lock strategy.
- Heavy database migration design.

Those can be added later only when user explicitly requests.
