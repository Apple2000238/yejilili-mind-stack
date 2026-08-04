"""消息幂等 / 去重模块

基于 message_id 或 content hash 检测重复请求，避免同一条用户消息
被重复写入 adapter_provenance 和 conversation_messages。
"""

from __future__ import annotations

import hashlib
import logging
from typing import Optional

logger = logging.getLogger("gateway.idempotency")

# 内存级去重缓存（LRU 风格，限制大小）
_MAX_CACHE_SIZE = 10_000
_seen_hashes: set[str] = set()


def compute_message_hash(
    session_id: str,
    role: str,
    content: str,
    message_index: Optional[int] = None,
) -> str:
    """
    计算消息指纹 hash。

    如果客户端提供了 message_index，则使用 session_id + index 作为唯一键；
    否则使用 session_id + role + content 的 hash。
    """
    if message_index is not None:
        key = f"{session_id}:{message_index}"
    else:
        key = f"{session_id}:{role}:{content}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def is_duplicate(message_hash: str) -> bool:
    """检查消息是否已处理过（内存缓存级别）。"""
    if message_hash in _seen_hashes:
        return True
    return False


def mark_seen(message_hash: str) -> None:
    """标记消息已处理。"""
    global _seen_hashes
    if len(_seen_hashes) >= _MAX_CACHE_SIZE:
        # 简单的淘汰策略：清空一半缓存（生产环境可用 OrderedDict/LRU）
        _seen_hashes = set(list(_seen_hashes)[_MAX_CACHE_SIZE // 2:])
    _seen_hashes.add(message_hash)


def check_idempotency(
    session_id: str,
    role: str,
    content: str,
    message_id: Optional[str] = None,
    message_index: Optional[int] = None,
) -> tuple[bool, str]:
    """
    检查消息是否是重复请求。

    返回: (is_duplicate, message_hash)
    - is_duplicate: True 表示已处理过，应跳过
    - message_hash: 用于后续数据库级幂等校验
    """
    # 优先使用客户端提供的 message_id
    if message_id:
        msg_hash = hashlib.sha256(f"{session_id}:{message_id}".encode("utf-8")).hexdigest()
    else:
        msg_hash = compute_message_hash(session_id, role, content, message_index)

    if is_duplicate(msg_hash):
        logger.info("duplicate message detected: session=%s hash=%s...", session_id, msg_hash[:16])
        return True, msg_hash

    mark_seen(msg_hash)
    return False, msg_hash
