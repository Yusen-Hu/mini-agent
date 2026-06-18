"""流式聊天回归测试：验证正常结束后消息能正确落库。"""
import uuid
import requests

BASE_URL = "http://127.0.0.1:8000/api"


def get_token():
    """注册 + 登录获取 token。"""
    username = f"test_stream_{uuid.uuid4().hex[:8]}"
    password = "test1234"

    requests.post(f"{BASE_URL}/auth/register", json={
        "username": username,
        "email": f"{username}@test.com",
        "password": password,
    })
    res = requests.post(f"{BASE_URL}/auth/login", json={
        "username": username,
        "password": password,
    })
    return res.json()["access_token"]


def test_stream_normal_completion():
    """Test 1：正常流式结束，应返回 session_uuid + [DONE]，消息落库。"""
    token = get_token()
    session_uuid = None

    res = requests.post(
        f"{BASE_URL}/chat/stream",
        json={"message": "你好，请简短回复"},
        headers={"Authorization": f"Bearer {token}"},
        stream=True,
    )
    assert res.status_code == 200

    full_text = ""
    got_session = False
    got_done = False

    for line in res.iter_lines(decode_unicode=True):
        if not line or not line.startswith("data: "):
            continue
        data = line[6:]
        if data == "[DONE]":
            got_done = True
            break
        if data.startswith("[SESSION:") and data.endswith("]"):
            session_uuid = data[9:-1]
            got_session = True
            continue
        full_text += data

    # 验证 SSE 协议
    assert got_session, "未收到 SESSION 标记"
    assert got_done, "未收到 [DONE] 标记"
    assert len(full_text) > 0, "AI 回复为空"

    # 验证消息落库
    msg_res = requests.get(
        f"{BASE_URL}/sessions/{session_uuid}/messages",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert msg_res.status_code == 200
    msg_data = msg_res.json()
    assert msg_data["total"] == 2, f"应有 2 条消息，实际 {msg_data['total']}"

    roles = [m["role"] for m in msg_data["messages"]]
    assert roles == ["user", "assistant"], f"角色顺序不对: {roles}"

    assistant_msg = msg_data["messages"][1]
    assert len(assistant_msg["content"]) > 0, "assistant 消息内容为空"
    print(f"[PASS] Test 1: session={session_uuid}, messages={msg_data['total']}")


def test_stream_with_session_id():
    """Test 2：带 session_id 发第二条消息，验证历史上下文 + 消息落库。"""
    token = get_token()
    session_uuid = None

    # 第一条消息（新建会话）
    res1 = requests.post(
        f"{BASE_URL}/chat/stream",
        json={"message": "记住这个数字：42"},
        headers={"Authorization": f"Bearer {token}"},
        stream=True,
    )
    for line in res1.iter_lines(decode_unicode=True):
        if line and line.startswith("data: ") and line[6:].startswith("[SESSION:"):
            session_uuid = line[6:][9:-1]

    assert session_uuid is not None, "第一条消息未返回 session_uuid"

    # 第二条消息（同一会话）
    full_text = ""
    res2 = requests.post(
        f"{BASE_URL}/chat/stream",
        json={"message": "我刚才说的数字是多少？", "session_id": session_uuid},
        headers={"Authorization": f"Bearer {token}"},
        stream=True,
    )
    for line in res2.iter_lines(decode_unicode=True):
        if not line or not line.startswith("data: "):
            continue
        data = line[6:]
        if data == "[DONE]":
            break
        if data.startswith("[SESSION:"):
            continue
        full_text += data

    assert len(full_text) > 0, "第二条回复为空"

    # 验证 DB 里有 4 条消息
    msg_res = requests.get(
        f"{BASE_URL}/sessions/{session_uuid}/messages",
        headers={"Authorization": f"Bearer {token}"},
    )
    msg_data = msg_res.json()
    assert msg_data["total"] == 4, f"应有 4 条消息，实际 {msg_data['total']}"
    print(f"[PASS] Test 2: session={session_uuid}, messages={msg_data['total']}")


if __name__ == "__main__":
    test_stream_normal_completion()
    test_stream_with_session_id()
    print("[ALL PASS]")
