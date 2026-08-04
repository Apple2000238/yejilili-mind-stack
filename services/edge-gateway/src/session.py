"""Session ID 提取与 namespace 稳定映射

从请求中解析 session/conversation ID，并维护到稳定 namespace 的映射，
确保多轮对话的连续性账本记录不会因客户端重连而断裂。
"""

from __future__ import annotations

import hashlib
import logging
from typing import Any, Optional

logger = logging.getLogger("gateway.session")

# 内存中的 namespace 缓存（重启后从数据库恢复）
_namespace_cache: dict[str, str] = {}


def _stable_hash(text: str) -> str:
    """生成稳定的短 hash 用于 namespace 派生。"""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def extract_session_id(body: dict[str, Any], headers: dict[str, str]) -> Optional[str]:
    """
    从请求 body 和 headers 中提取 session ID。

    优先级（从高到低）：
    1. body["session_id"]
    2. body["sessionId"]
    3. body["metadata"]["session_id"]
    4. headers["x-session-id"]
    5. headers["x-conversation-id"]
    """
    # Body 内直接字段
    for key in ("session_id", "sessionId"):
        if key in body and body[key]:
            return str(body[key])

    # Body metadata
    metadata = body.get("metadata") or {}
    if isinstance(metadata, dict):
        for key in ("session_id", "sessionId", "conversation_id"):
            if key in metadata and metadata[key]:
                return str(metadata[key])

    # Headers
    for key in ("x-session-id", "x-conversation-id"):
        value = headers.get(key) or headers.get(key.replace("-", "_"))
        if value:
            return str(value)

    return None


def get_or_create_namespace(
    session_id: str,
    platform: str = "unknown",
    room: str = "default",
) -> str:
    """
    将 session_id 映射到稳定的 namespace。

    规则：
    - 如果 session_id 已存在于缓存，直接返回对应 namespace
    - 否则按 platform + room + session_id 派生一个确定性 namespace
    - 返回格式: {platform}/{room}/{short_hash}
    """
    if session_id in _namespace_cache:
        return _namespace_cache[session_id]

    # 确定性派生 namespace
    short_hash = _stable_hash(f"{platform}:{room}:{session_id}")
    namespace = f"{platform}/{room}/{short_hash}"
    _namespace_cache[session_id] = namespace

    logger.debug("namespace mapped: session_id=%s -> namespace=%s", session_id, namespace)
    return namespace


def resolve_session_namespace(
    body: dict[str, Any],
    headers: dict[str, str],
    default_platform: str = "unknown",
) -> tuple[Optional[str], Optional[str]]:
    """
    一站式解析：从请求中提取 session_id 和对应的 namespace。

    返回: (session_id, namespace)
    如果无法提取 session_id，则返回 (None, None)
    """
    session_id = extract_session_id(body, headers)
    if not session_id:
        return None, None

    platform = default_platform
    # 尝试从 metadata 或 headers 中提取 platform
    metadata = body.get("metadata") or {}
    if isinstance(metadata, dict):
        platform = metadata.get("platform", platform)
    if "x-platform" in headers:
        platform = headers["x-platform"]

    room = metadata.get("room", "default") if isinstance(metadata, dict) else "default"
    if "x-room" in headers:
        room = headers["x-room"]

    namespace = get_or_create_namespace(session_id, platform, room)
    return session_id, namespace
