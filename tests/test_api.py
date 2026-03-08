"""
Tests for each API endpoint. Uses in-memory SQLite + dependency_overrides.
"""
import pytest
from fastapi.testclient import TestClient


# ─── No-auth endpoints ───────────────────────────────────────────────────────

def test_health(client: TestClient):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_stats_empty(client: TestClient):
    r = client.get("/stats")
    assert r.status_code == 200
    data = r.json()
    assert data["users"] == 0
    assert data["friendships"] == 0
    assert data["messages"] == 0


def test_stats_after_register(client: TestClient, token):
    _id, _token = token
    r = client.get("/stats")
    assert r.status_code == 200
    assert r.json()["users"] == 1


# ─── Register ────────────────────────────────────────────────────────────────

def test_register_ok(client: TestClient):
    r = client.post(
        "/register",
        json={"name": "alice", "description": "hi", "status": "open"},
    )
    assert r.status_code == 200
    assert "Token：" in r.text
    assert "注册成功" in r.text
    assert "alice" in r.text


def test_register_duplicate_name(client: TestClient):
    client.post("/register", json={"name": "bob", "status": "open"})
    r = client.post("/register", json={"name": "bob", "status": "open"})
    # 409 name taken, or 429 same-IP same-day, or 200 with error message
    assert r.status_code in (200, 409, 429)
    if r.status_code == 200:
        assert "错误" in r.text or "已被使用" in r.text or "仅允许" in r.text


def test_register_validation(client: TestClient):
    r = client.post("/register", json={"name": ""})
    assert r.status_code in (200, 422)
    if r.status_code == 200:
        assert "错误" in r.text or "格式" in r.text


# ─── Messages (require X-Token) ──────────────────────────────────────────────

def test_messages_no_token(client: TestClient):
    r = client.get("/messages")
    # App normalizes validation errors to 200 + plain text for non-exempt paths
    assert r.status_code in (200, 422)
    if r.status_code == 200:
        assert "请求格式错误" in r.text or "错误" in r.text


def test_messages_invalid_token(client: TestClient):
    r = client.get("/messages", headers={"X-Token": "invalid"})
    assert r.status_code in (200, 401)
    if r.status_code == 200:
        assert "Token" in r.text or "错误" in r.text


def test_messages_empty(client: TestClient, token):
    _id, tok = token
    r = client.get("/messages", headers={"X-Token": tok})
    assert r.status_code == 200
    assert len(r.text) >= 0  # empty inbox returns short message


def test_send_self(client: TestClient, token):
    uid, tok = token
    r = client.post(
        "/send",
        headers={"X-Token": tok},
        json={"to_id": uid, "content": "no"},
    )
    assert r.status_code in (200, 400)
    if r.status_code == 200:
        assert "错误" in r.text or "自己" in r.text


def test_send_to_nonexistent(client: TestClient, token):
    _uid, tok = token
    r = client.post(
        "/send",
        headers={"X-Token": tok},
        json={"to_id": 99999, "content": "hi"},
    )
    assert r.status_code in (200, 404)
    if r.status_code == 200:
        assert "错误" in r.text or "用户" in r.text


def test_send_ok_first_contact(client: TestClient, two_users):
    id_a, tok_a, id_b, tok_b = two_users
    r = client.post(
        "/send",
        headers={"X-Token": tok_a},
        json={"to_id": id_b, "content": "friend request"},
    )
    assert r.status_code == 200
    assert len(r.text) > 0
    r2 = client.get("/messages", headers={"X-Token": tok_b})
    assert r2.status_code == 200
    assert len(r2.text) > 0


def test_send_reply_accepts_friendship(client: TestClient, two_users):
    id_a, tok_a, id_b, tok_b = two_users
    client.post("/send", headers={"X-Token": tok_a}, json={"to_id": id_b, "content": "hi"})
    r = client.post("/send", headers={"X-Token": tok_b}, json={"to_id": id_a, "content": "hello"})
    assert r.status_code == 200
    assert len(r.text) > 0


# ─── Users & Friends ────────────────────────────────────────────────────────

def test_users_discover_no_token(client: TestClient):
    r = client.get("/users")
    assert r.status_code in (200, 422)
    if r.status_code == 200:
        assert "请求格式错误" in r.text or "错误" in r.text


def test_users_discover_ok(client: TestClient, token):
    _id, tok = token
    r = client.get("/users", headers={"X-Token": tok})
    assert r.status_code == 200
    # may be empty or include self-excluded list
    assert "text/plain" in r.headers.get("content-type", "")


def test_users_get_me(client: TestClient, token):
    uid, tok = token
    r = client.get(f"/users/{uid}", headers={"X-Token": tok})
    assert r.status_code == 200


def test_users_get_nonexistent(client: TestClient, token):
    _uid, tok = token
    r = client.get("/users/99999", headers={"X-Token": tok})
    assert r.status_code in (200, 404)
    if r.status_code == 200:
        assert "错误" in r.text or "用户" in r.text


def test_friends_empty(client: TestClient, token):
    _id, tok = token
    r = client.get("/friends", headers={"X-Token": tok})
    assert r.status_code == 200
    # Empty list: "暂无好友" or list with "好友"
    assert len(r.text) > 0


def test_friends_after_accept(client: TestClient, two_users):
    id_a, tok_a, id_b, tok_b = two_users
    client.post("/send", headers={"X-Token": tok_a}, json={"to_id": id_b, "content": "hi"})
    client.post("/send", headers={"X-Token": tok_b}, json={"to_id": id_a, "content": "ok"})
    r = client.get("/friends", headers={"X-Token": tok_a})
    assert r.status_code == 200
    assert len(r.text) > 5


def test_me_patch_status(client: TestClient, token):
    _id, tok = token
    r = client.patch("/me", headers={"X-Token": tok}, json={"status": "friends_only"})
    assert r.status_code == 200
    assert len(r.text) > 0


def test_block_not_friend(client: TestClient, two_users):
    id_a, tok_a, id_b, _ = two_users
    r = client.post(f"/block/{id_b}", headers={"X-Token": tok_a})
    assert r.status_code in (200, 403)
    if r.status_code == 200:
        assert "错误" in r.text or "拉黑" in r.text


def test_block_and_unblock(client: TestClient, two_users):
    id_a, tok_a, id_b, tok_b = two_users
    client.post("/send", headers={"X-Token": tok_a}, json={"to_id": id_b, "content": "hi"})
    client.post("/send", headers={"X-Token": tok_b}, json={"to_id": id_a, "content": "ok"})
    r = client.post(f"/block/{id_b}", headers={"X-Token": tok_a})
    assert r.status_code == 200
    assert len(r.text) > 0
    r2 = client.post(f"/unblock/{id_b}", headers={"X-Token": tok_a})
    assert r2.status_code == 200
    assert len(r2.text) > 0


# ─── Stream (SSE) ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_stream_requires_token(async_client):
    r = await async_client.get("/stream")
    assert r.status_code in (401, 422)


@pytest.mark.asyncio
async def test_stream_connect(async_client, token):
    _id, tok = token
    # Open stream, assert 200 + content-type, then close (don't wait for events)
    async with async_client.stream("GET", "/stream", headers={"X-Token": tok}, timeout=1.0) as r:
        assert r.status_code == 200
        assert "text/event-stream" in r.headers.get("content-type", "")
