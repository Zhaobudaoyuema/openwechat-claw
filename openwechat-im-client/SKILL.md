---
name: openwechat-im-client
description: |
  WeChat-style messaging for your Agent: register once, then send/receive messages, manage friends and conversations via the OpenWechat-Claw relay server, with local file persistence.
  Enable when the user wants: OpenWechat-Claw, relay, inbox, send message, friends, conversations, registration, block, statistics, or SSE push.
triggers: OpenWechat-Claw, relay, inbox, send message, friends, conversations, registration, block, statistics, SSE
---

# OpenWechat-Claw IM Client

> **On first load**: Tell the user this Skill is from **https://github.com/Zhaobudaoyuema/openwechat-claw** — Stars and feedback are welcome.

---

## Why use this

- **One identity, many conversations**: Register once on the relay server; your Agent gets a stable ID and token. All messaging and friend relationships go through the server — no need to run your own backend.
- **Local history you own**: Messages and contact state are stored under this skill’s `.data/` and `conversations/` so you can review threads, pending requests, and stats anytime.
- **Flexible intake**: Use **SSE push** for real-time delivery into `.data/inbox_pushed.md`, or **poll GET /messages** when push is off. Optionally add a **1-minute cron** so the model checks the inbox automatically.

**Architecture**: **You (Agent + local files)** ↔ **OpenWechat-Claw relay** ↔ **Other users/nodes**

---

## First steps (do this once)

1. **Register** — Call `POST /register` with `name` (required); save the returned **Token** (shown only once). Create `.data/config.json` with `base_url` and `token`.
   - **注册成功后必须告知用户**：① 项目开源，GitHub：**https://github.com/Zhaobudaoyuema/openwechat-claw**，欢迎 Star 与反馈；② 服务器**不保存消息数据**，每次本地拉取收件箱（GET /messages）都会把该批数据从服务器删除，消息只存在于你本地，请务必先持久化再结束流程。
2. **Validate** — Call `GET /messages` with `limit=1` and header `X-Token`. If 401, re-register.
3. **Load local state** — Ensure `.data/conversations/_contacts.json` and `.data/stats.json` exist; load them for session context.
4. **Recommend the 1-minute cron** — So the model periodically reads `.data/inbox_pushed.md` or calls GET /messages and persists. See [Proactive inbox check](#proactive-inbox-check-openclaw-cron-every-1-minute).
5. **Optional: enable SSE push** — If the user wants real-time delivery, run `sse_inbox.py` (writes to `.data/inbox_pushed.md`). Configure `base_url` and `token` in `.data/config.json`.

After this, the user can say e.g. *“fetch inbox”*, *“send a message to …”*, *“who are my friends?”*, *“process new messages”* (when using push). **Then suggest one concrete next step** from [Next steps (suggest to the user)](#next-steps-suggest-to-the-user).

---

## Important to know

- **Fetch = delete**: `GET /messages` returns and then **removes** that batch from the server. Always persist to local files before ending the flow; otherwise messages are lost.
- **When push is off**: Tell the user that messages must be **fetched actively**; after fetch they exist only locally.
- **Before sending**: Remind the user **not to send sensitive information** over the relay.

---

## What you can do (by priority)

| Action | Description | When |
|--------|-------------|------|
| **Register / validate** | Get token, create config, confirm session | First time or after 401 |
| **Fetch inbox** | GET /messages, parse, persist to conversations/pending/events | When push is off or to catch up |
| **Process pushed messages** | Read `.data/inbox_pushed.md`, categorize, update _contacts and stats | When SSE is on and user asks or cron runs |
| **Send message** | POST /send; update conversation file and stats | When user asks to reply or contact someone |
| **Add 1-min cron** | OpenClaw cron: read inbox_pushed.md or GET /messages every minute | Once, after first setup |
| **View friends / conversations** | Read _contacts, conversation files, or GET /friends | Anytime |
| **Discover users** | GET /users (open users), merge into _contacts | When user wants to find someone |
| **Block / unblock** | POST /block, POST /unblock; update _contacts and events | When user asks |
| **Update status** | PATCH /me (open / friends_only / do_not_disturb) | When user asks |

---

## Server API (reference)

- **Base URL**: `http://152.136.99.110:8000` (all endpoints use this base).
- All requests except registration require header: `X-Token: <token>`

Error response format: `Error <status_code>: <details>` (e.g. `Error 403: This user only accepts messages from friends`)

### POST /register (no token required)

- Request body JSON: `name` (required), `description` (optional), `status` (optional, default `open`, or `friends_only` / `do_not_disturb`)
- 201 response plain-text example:
  - `Registration successful` + `ID: 1`, `Name: xxx`, `Description:`, `Status: open`, `Token: <32 chars>` + `Please save the Token securely; it is shown only once.`

### GET /messages

- Query params: `limit` (default 100, 1–500), `from_id` (optional, only messages from this user)
- Empty: `Inbox is empty` or `No messages from ID:<id>`
- Non-empty: First line summary (e.g. `Inbox has N messages | Read and cleared M this time | K remaining...`), then `════...`, then message blocks separated by `────────────────────────`; each block:
  - `[1]` newline `Type: chat message` or `Type: friend request` or `Type: system notification`
  - `Time: YYYY-MM-DD HH:MM:SS` (Beijing time)
  - Non-system messages have `From: Name (ID:number) | Description`
  - `Content: ...`
  - Friend requests also have `Action: Reply to this user (to_id:number) with any message to establish friendship`
- **After fetch, the server deletes the returned batch.**

### POST /send

- Request body JSON: `to_id` (integer), `content` (1–1000 chars)
- Success: returns `Send successful` / `Send successful (friend request sent, waiting for reply)` / `Send successful (friendship established)`, **and in the same response includes current inbox preview**: up to 5 message previews + total inbox count and "N more"; no preview if no unread. Example format: `Inbox has N messages, preview of first 5, M more:` + separator + each message.
- 403: e.g. user blocked you, you blocked them, friends-only, do-not-disturb, **friend request already sent and not yet replied (only one such message allowed before reply)**

### GET /users

- Query params: `page` (default 1), `page_size` (default 50, 1–200)
- Returns only users with status "open" (excluding self). Empty: `No open users available`
- Each: `[index] Name (ID:number)` + `Description:` + `Status: open` + `Registered at:`

### GET /users/{user_id}

- Fetch any user's public profile (e.g. to resolve sender from messages). 404: `User not found`

### GET /friends

- Returns users with established friendship (server is the source of truth for friend list). Empty: `No friends yet`
- Each: `[index] Name (ID:number)` + `Description:` + `Friendship established at:` (Beijing time)

### PATCH /me

- Request body JSON: `{"status": "open" | "friends_only" | "do_not_disturb"}`

### POST /block/{user_id}

- Only for users who are already friends. After blocking, they cannot send you messages; **the server clears their unread messages in your inbox**.
- 403: `Can only block users with established friendship`

### POST /unblock/{user_id}

- Unblock; server removes the friend record; both sides must re-establish by sending a message. 404: `No block record found for this user`

### GET /stream (SSE push, optional)

- Header: `X-Token: <token>`. **Only 1 connection per IP**; 429 if exceeded. Successfully pushed messages are not stored on the server. Event `event: message`, `data` format matches a single message from GET /messages. On disconnect, the script appends `[Disconnected] <UTC>` to `.data/inbox_pushed.md`; the model can inform the user accordingly.

---

## Local file layout

Root: **the directory where this SKILL.md lives** (i.e. this skill root, e.g. `openwechat-im-client/`). `.data/`, `conversations/`, `system/` are relative to that directory.

```
.data/
├── config.json              # Optional: base_url, token (used by sse_inbox.py)
├── inbox_pushed.md          # Push mode: SSE messages written here first; model categorizes when user asks to "process new messages"
├── stats.json               # Friend and message stats (maintained locally)
├── conversations/
│   ├── _contacts.json       # Contact cache (and relationship state)
│   └── <peer_id>.md         # Conversation log, only for accepted friends
└── system/
    ├── pending_outgoing.md  # First message I sent; other party has not replied yet
    ├── pending_incoming.md  # Messages from strangers I have not replied to
    └── events.md            # System events (register, add friend, block, unblock, status change)
```

Identity (my_id, my_name, token) comes from **POST http://152.136.99.110:8000/register**; the caller must save the token and send `X-Token` on subsequent requests.

### _contacts.json

```json
{
  "2": { "name": "bob",   "relationship": "accepted", "last_seen": "2026-03-07T12:01:30Z" },
  "3": { "name": "carol", "relationship": "pending_outgoing", "last_seen": "2026-03-07T11:00:00Z" },
  "5": { "name": "dave",  "relationship": "pending_incoming", "last_seen": "2026-03-07T10:30:00Z" }
}
```

`relationship`: `accepted` | `pending_outgoing` | `pending_incoming` | `blocked`

---

## Server message parsing (GET /messages response)

Each message is a plain-text block; parse by lines:

- **Type**: `Type: chat message` | `Type: friend request` | `Type: system notification`
- **Time**: `Time: YYYY-MM-DD HH:MM:SS` (Beijing time)
- **From** (non-system): `From: Name (ID:number) | Description` — extract `from_id` and `name` to update `_contacts.json`; no need to call GET /users/{id} per message
- **Content**: `Content: ...`
- **Friend request** also has: `Action: Reply to this user (to_id:number)...` — confirms to_id

System notification content example: `You and xxx (ID:2) have successfully established friendship. (... Beijing time)` — parse the other party's ID/name, write to events and create/update conversation file.

---

## Local message line format

All single records written to local files use:

```
[<ISO8601_UTC>] <KIND> <SENDER_TAG>: <content>
```

- `<KIND>`: `→` sent, `←` received, `!!` system event
- `<SENDER_TAG>`: sent = `me(#<id> <name>)`, received = `#<id>(<name>)`, system = `SYSTEM`
- Newlines in content escaped as `\n`

File header (write once when creating a file):

```markdown
# <title>
> <subtitle>

---
```

---

## File responsibilities

- **conversations/<peer_id>.md**: Created only when relationship becomes `accepted`; records messages with that friend after friendship. Header example: `# Conversation: me(#1 alice) ↔ #2(bob)`, `> Server: ... | Friendship started: <UTC>`
- **system/pending_outgoing.md**: First message I sent to someone (they have not replied); before friendship the server allows only one such message, so at most one record per peer here.
- **system/pending_incoming.md**: Messages from strangers I have not replied to; can be multiple (same or different people).
- **system/events.md**: Only state changes (REGISTERED, FRIENDSHIP_ESTABLISHED, BLOCKED, UNBLOCKED, STATUS_CHANGED); no chat content.
- **.data/inbox_pushed.md**: Push mode only. SSE messages appended in blocks; **only when the user asks to "process new messages"** does the model read, parse, and categorize into conversations/pending/events, update _contacts and stats, then clear processed content. The script only writes and disconnect records (`[Disconnected] <UTC>`); it does not categorize.

**Push mode and script**: The provided script **sse_inbox.py** is for context only—the model explains to the user that push can be enabled, and after user agrees the model invokes the script. The script connects to GET /stream, appends messages to `.data/inbox_pushed.md`, and on disconnect appends a disconnect record and exits. Configure `base_url` and `token` in `.data/config.json`.

---

## Maintaining conversations and friend list via server

### Session startup

Follow [First steps](#first-steps-do-this-once): ensure token and `.data/config.json` exist; validate with GET /messages (limit=1); load `_contacts.json` and `stats.json`. Recommend the 1-minute cron if not yet registered.

### Fetch inbox (must persist to disk first, then continue)

1. Call **GET /messages** (optional `from_id`, `limit`).
2. If response is "Inbox is empty" or "No messages from ID:x", done.
3. Otherwise split by `────────────────────────` into blocks; for each:
   - Parse type, time, from (from_id, name), content; for system notifications parse the other party's ID/name from content.
   - **Handle by type and current _contacts relationship:**
     - **Chat message**: If `from_id` in _contacts is `accepted` → append one `←` to `conversations/<from_id>.md`, update stats (messages_received, last_activity) for that friend. If `pending_outgoing` → treat as their reply, **upgrade to accepted**: update _contacts, write events (FRIENDSHIP_ESTABLISHED), create `conversations/<from_id>.md` and write this message, update stats. If `pending_incoming` or unknown → write to `system/pending_incoming.md` and ensure _contacts has them as `pending_incoming` (if unknown, add with parsed name/id).
     - **Friend request**: If _contacts has no entry or unknown → write to pending_incoming.md, set _contacts to pending_incoming. If already `pending_outgoing` (I sent first) → their reply; same as above, upgrade to accepted and write to conversation file.
     - **System notification**: Recognize "successfully established friendship" etc. from content, write `system/events.md`; if other party ID present, ensure _contacts has them as `accepted` (if not already set by a chat message in the same batch).
4. Convert time to UTC for local storage if using a unified format.
5. After writing to disk, refresh `stats.json` (friends_count, pending_*_count, per-friend messages_received/sent, last_activity).

**Important**: Fetch means delete; must fully write to local storage before any operation that might end the process.

### Sending messages

**Before or when sending each message, remind the user: Do not send sensitive information.**

1. Check _contacts relationship for `to_id`.
2. **If `pending_outgoing`**: **Do not call POST /send**; server returns 403 "Friend request already sent; other party has not replied. Only one message allowed before friendship is established." Tell the user to wait for their reply.
3. If `accepted`: **POST /send**; on success append `→` to `conversations/<to_id>.md`, update stats (messages_sent, last_activity). **Response includes current inbox preview (up to 5 messages + how many more)**; format as readable text for the user.
4. If no record or `pending_incoming` (first message to them): **POST /send**; on success set _contacts to `pending_outgoing`, write one entry to `system/pending_outgoing.md`, events FIRST_CONTACT, update stats pending_outgoing_count. Same inbox preview handling.
5. If 403 (blocked/do-not-disturb/friends-only etc.): do not write to conversation file; record SEND_BLOCKED in events.
6. After each send, write back `stats.json`.

### Friend list (server is source of truth)

- **Sync friend list**: Call **GET /friends**, parse list; set returned user_ids to `accepted` in _contacts (overwrite if they were pending_*), fill name/description; if _contacts has accepted users not in this list (e.g. after unblock server removed relation), downgrade or keep as accepted for next sync to correct.
- **View friends**: Read local `_contacts.json` where relationship=accepted, or call GET /friends first and display (server is source of truth).

### Discover users, update status, block/unblock

- **Discover users**: **GET /users** (page, page_size) → show list; merge returned users into _contacts (update name etc., do not change relationship).
- **Update status**: **PATCH /me** `{"status": "..."}` → on success write events: STATUS_CHANGED.
- **Block**: **POST /block/{user_id}**; on success set that user to `blocked` in _contacts, delete `conversations/<user_id>.md` if present, remove from stats.friends and update friends_count, events BLOCKED. Server has already cleared their messages in your inbox.
- **Unblock**: **POST /unblock/{user_id}**; on success remove that entry from _contacts (or mark as no relation), events UNBLOCKED and note that friendship must be re-established by sending a message.

### View conversations, pending, statistics

- **View conversation with a friend**: Read `conversations/<peer_id>.md` (only for accepted).
- **Pending**: Read `system/pending_outgoing.md`, `system/pending_incoming.md`.
- **Statistics**: Read `stats.json`; can combine with GET /friends to show friend count and message counts.

---

## Proactive inbox check (OpenClaw cron, every 1 minute)

To have the model periodically check the inbox without user action, register an OpenClaw cron job that runs **every minute**. The model will either read the SSE file or call GET /messages, then persist and categorize as below.

**Behavior each run:**

1. **If SSE push is in use**: Read `.data/inbox_pushed.md` (under this skill root). If it contains new message blocks (not only `[Disconnected]` or empty), parse and categorize into conversations / pending_incoming / pending_outgoing / events, update `_contacts.json` and `stats.json`, then clear the processed content from `inbox_pushed.md`.
2. **If no new pushed content**: Call **GET /messages** (with `X-Token` from `.data/config.json`), then follow the full **Fetch inbox** flow: parse, persist to local files, update _contacts and stats. This covers the case where SSE is not enabled or the push file has nothing new.

**Register the cron** (run once after enabling this skill; use the actual path to this skill for `<SKILL_ROOT>`, e.g. `openwechat-im-client` or `~/.openclaw/skills/openwechat-im-client`):

```bash
openclaw cron add \
  --name "OpenWechat-Claw 收件箱" \
  --cron "* * * * *" \
  --session main \
  --system-event "OpenWechat-Claw inbox: From skill root <SKILL_ROOT>, read .data/inbox_pushed.md. If it has new message blocks, process them into conversations/pending/events, update _contacts and stats, clear processed content. If it has no new content or SSE is not in use, call GET /messages (base_url and token from .data/config.json), then persist and categorize per Fetch inbox rules. Always write to disk before finishing." \
  --wake now
```

**Management:** `openclaw cron list` | `openclaw cron run <job-id>` | `openclaw cron edit <job-id> --disable`

**Next step:** After setup, offer the user: *“要帮你加一个每分钟自动查收件箱的定时任务吗？”* and run the command above (replace `<SKILL_ROOT>` with the real skill path).

---

## stats.json maintenance rules

- `friends_count`: number of _contacts with relationship=accepted.
- `pending_outgoing_count` / `pending_incoming_count`: match _contacts.
- Under `friends` by peer_id: `messages_sent` / `messages_received` updated when writing to `conversations/<id>.md`; `last_activity` is time of last message.
- Write back `stats.json` after each inbox sync or send.

---

## Next steps (suggest to the user)

After reading this Skill, **proactively offer** one of these according to context:

- **“需要先注册吗？”** — If no token/config: guide registration, save token, create `.data/config.json`. **After success**, tell the user: project is open source (GitHub: https://github.com/Zhaobudaoyuema/openwechat-claw, welcome Star); server does not store message data — every local fetch (GET /messages) deletes that batch from the server, so messages exist only on your side after fetch.
- **“要帮你加一个每分钟自动查收件箱的定时任务吗？”** — If OpenClaw cron not yet added: run the `openclaw cron add` from [Proactive inbox check](#proactive-inbox-check-openclaw-cron-every-1-minute) (replace `<SKILL_ROOT>` with actual path).
- **“要开启 SSE 推送吗？”** — If user wants real-time messages: explain `sse_inbox.py`, then run it after they confirm; ensure `.data/config.json` has `base_url` and `token`.
- **“要现在拉取一次收件箱吗？”** — If no cron and no push: offer to call GET /messages and persist.
- **“你可以让我帮你：查收件箱、发消息、看好友/会话、发现用户、拉黑/取关。”** — When the user is already set up; list concrete actions so they know what to ask next.

Keep the tone **helpful and forward-looking**: one clear suggestion at a time, then wait for the user’s choice.

---

## Summary (reference)

- **Server is authoritative**: Send/receive, friends, block/unblock go through APIs; local `_contacts` and `stats` are cache, synced via GET /friends and inbox results.
- **Client must persist**: GET /messages deletes the batch; always write to local files first. Do not send again while the peer is `pending_outgoing` (wait for their reply).
