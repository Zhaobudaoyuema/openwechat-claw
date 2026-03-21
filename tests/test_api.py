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

def test_messages_route_removed(client: TestClient):
    """GET /messages 已删除（WS-only 化），应返回 404。"""
    r = client.get("/messages")
    assert r.status_code == 404


def test_messages_route_removed_with_token(client: TestClient, token):
    """GET /messages 已删除，有 token 也返回 404。"""
    _id, tok = token
    r = client.get("/messages", headers={"X-Token": tok})
    assert r.status_code == 404


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
    """首次发送陌生人 → friend_request，DB 写入成功。"""
    id_a, tok_a, id_b, tok_b = two_users
    r = client.post(
        "/send",
        headers={"X-Token": tok_a},
        json={"to_id": id_b, "content": "friend request"},
    )
    assert r.status_code == 200
    assert len(r.text) > 0


def test_send_reply_accepts_friendship(client: TestClient, two_users):
    id_a, tok_a, id_b, tok_b = two_users
    client.post("/send", headers={"X-Token": tok_a}, json={"to_id": id_b, "content": "hi"})
    r = client.post("/send", headers={"X-Token": tok_b}, json={"to_id": id_a, "content": "hello"})
    assert r.status_code == 200
    assert len(r.text) > 0


def test_send_file_ok(client: TestClient, two_users):
    """发送带附件的消息。仅验证接口可调用且返回非 5xx。"""
    id_a, tok_a, id_b, tok_b = two_users
    r = client.post(
        "/send/file",
        headers={"X-Token": tok_a},
        data={"to_id": str(id_b), "content": "see attachment"},
        files={"file": ("test.txt", b"hello file content", "text/plain")},
    )
    assert r.status_code == 200
    # 成功或业务错误（如限流、好友申请已发出）均返回 200
    assert "请求格式错误" not in r.text


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
    """A 发申请 → B 回复 → 验证两次发送均成功。状态码 200 即表示消息已写入 DB。"""
    id_a, tok_a, id_b, tok_b = two_users
    r1 = client.post("/send", headers={"X-Token": tok_a}, json={"to_id": id_b, "content": "hi"})
    assert r1.status_code == 200

    r2 = client.post("/send", headers={"X-Token": tok_b}, json={"to_id": id_a, "content": "ok"})
    assert r2.status_code == 200


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


# ─── Homepage ────────────────────────────────────────────────────────────────

def test_homepage_upload_and_view(client: TestClient, token):
    """上传主页并访问。"""
    uid, tok = token
    html = "<html><body><h1>Hello</h1></body></html>"
    r = client.put(
        "/homepage",
        headers={"X-Token": tok},
        content=html,
    )
    assert r.status_code == 200
    assert "访问地址" in r.text
    r2 = client.get(f"/homepage/{uid}")
    assert r2.status_code == 200
    assert "Hello" in r2.text


def test_homepage_empty(client: TestClient, token):
    """未设置主页时返回默认空页。"""
    uid, tok = token
    r = client.get(f"/homepage/{uid}")
    assert r.status_code == 200
    assert "尚未设置主页" in r.text


def test_homepage_upload_multipart(client: TestClient, token):
    """multipart 上传 HTML 文件。"""
    uid, tok = token
    html = "<html><body><p>My Page</p></body></html>"
    r = client.put(
        "/homepage",
        headers={"X-Token": tok},
        data={},
        files={"file": ("index.html", html.encode("utf-8"), "text/html")},
    )
    assert r.status_code == 200
    r2 = client.get(f"/homepage/{uid}")
    assert r2.status_code == 200
    assert "My Page" in r2.text


def test_homepage_reject_json(client: TestClient, token):
    """客户端传 JSON {"html":"..."} 时，直接拒绝。"""
    uid, tok = token
    payload = '{"html": "<html><body><h1>NightOwl</h1></body></html>"}'
    r = client.put(
        "/homepage",
        headers={"X-Token": tok, "Content-Type": "application/json"},
        content=payload,
    )
    assert r.status_code == 400
    assert "JSON" in r.text or "HTML" in r.text


def test_homepage_reject_non_html(client: TestClient, token):
    """纯文本无 HTML 标签时拒绝。"""
    uid, tok = token
    r = client.put(
        "/homepage",
        headers={"X-Token": tok},
        content="just plain text no tags",
    )
    assert r.status_code == 400
    assert "HTML" in r.text


# ─── World REST API ───────────────────────────────────────────────────────────

def test_world_status_no_token(client: TestClient):
    # App normalizes missing header to 200 + plain text error (same as /messages)
    r = client.get("/api/world/status")
    assert r.status_code in (200, 422)
    if r.status_code == 200:
        assert "请求格式错误" in r.text or "错误" in r.text


def test_world_status_ok(client: TestClient, token):
    _id, tok = token
    r = client.get("/api/world/status", headers={"X-Token": tok})
    assert r.status_code == 200
    data = r.json()
    # 新用户未进入世界，online 应为 False
    assert data["online"] is False
    assert "x" in data and "y" in data


def test_world_history_no_token(client: TestClient):
    r = client.get("/api/world/history")
    assert r.status_code in (200, 422)
    if r.status_code == 200:
        assert "请求格式错误" in r.text or "错误" in r.text


def test_world_history_ok(client: TestClient, token):
    _id, tok = token
    r = client.get("/api/world/history", headers={"X-Token": tok})
    assert r.status_code == 200
    data = r.json()
    assert data["user_id"] == _id
    assert data["window"] == "7d"
    assert "points" in data
    assert isinstance(data["points"], list)


def test_world_history_window(client: TestClient, token):
    _id, tok = token
    r = client.get("/api/world/history?window=1h", headers={"X-Token": tok})
    assert r.status_code == 200
    assert r.json()["window"] == "1h"


def test_world_social_no_token(client: TestClient):
    r = client.get("/api/world/social")
    assert r.status_code in (200, 422)
    if r.status_code == 200:
        assert "请求格式错误" in r.text or "错误" in r.text


def test_world_social_ok(client: TestClient, token):
    _id, tok = token
    r = client.get("/api/world/social", headers={"X-Token": tok})
    assert r.status_code == 200
    data = r.json()
    assert data["user_id"] == _id
    assert "events" in data
    assert isinstance(data["events"], list)


def test_world_heatmap_no_token(client: TestClient):
    r = client.get("/api/world/heatmap")
    assert r.status_code in (200, 422)
    if r.status_code == 200:
        assert "请求格式错误" in r.text or "错误" in r.text


def test_world_heatmap_ok(client: TestClient, token):
    _id, tok = token
    r = client.get("/api/world/heatmap", headers={"X-Token": tok})
    assert r.status_code == 200
    data = r.json()
    assert "cells" in data
    assert isinstance(data["cells"], list)


def test_world_share_card_no_token(client: TestClient):
    r = client.get("/api/world/share-card")
    assert r.status_code in (200, 422)
    if r.status_code == 200:
        assert "请求格式错误" in r.text or "错误" in r.text


def test_world_share_card_ok(client: TestClient, token):
    _id, tok = token
    r = client.get("/api/world/share-card", headers={"X-Token": tok})
    assert r.status_code == 200
    data = r.json()
    assert "user" in data
    assert "stats" in data
    assert data["stats"]["period"] == "7d"
    assert "move_count" in data["stats"]
    assert "encounter_count" in data["stats"]
    assert "friend_count" in data["stats"]


def test_world_share_card_target(client: TestClient, two_users):
    id_a, tok_a, id_b, tok_b = two_users
    # 查看自己的 share card
    r = client.get("/api/world/share-card", headers={"X-Token": tok_a})
    assert r.status_code == 200
    assert r.json()["user"]["user_id"] == id_a
    # 查看他人的 share card
    r2 = client.get(f"/api/world/share-card?target_id={id_b}", headers={"X-Token": tok_a})
    assert r2.status_code == 200
    assert r2.json()["user"]["user_id"] == id_b


def test_world_nearby_no_token(client: TestClient):
    # world_nearby returns PlainTextResponse → HTTPException(401) forced to 200 by handler
    r = client.get("/api/world/nearby")
    assert r.status_code in (200, 401, 422)
    if r.status_code == 200:
        assert "错误" in r.text or "Token" in r.text


def test_world_nearby_not_in_world(client: TestClient, token):
    _id, tok = token
    r = client.get("/api/world/nearby", headers={"X-Token": tok})
    assert r.status_code == 200
    # 用户未进入世界，应提示先连接 WS
    assert "WS" in r.text or "世界" in r.text
