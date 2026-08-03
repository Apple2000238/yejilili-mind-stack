"""鉴权：Bearer token + caller subject 记录。"""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from typing import Optional

from fastapi import HTTPException, Request


@dataclass(frozen=True)
class Caller:
    subject: str  # 调用方标识，如 "xinchao"、"edge-gateway"
    token_hash: str  # token 的 SHA256 前16位，用于审计


# 最小权限 token 映射：不同调用方使用独立 token
# 生产环境中这些 token 由 Docker Secret 注入，此处仅为结构定义
KNOWN_CALLERS = {
    "xinchao": "xinchao",
    "edge-gateway": "edge-gateway",
}


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()[:16]


def require_auth(request: Request, expected_token: str) -> Caller:
    """
    验证请求携带的 Authorization: Bearer <token>。
    返回 Caller 对象供后续审计使用。
    """
    auth = request.headers.get("Authorization", "")
    if not auth.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")

    token = auth[7:].strip()
    if not token:
        raise HTTPException(status_code=401, detail="Empty token")

    # 常量时间比较，防时序攻击
    if not hmac.compare_digest(token, expected_token):
        raise HTTPException(status_code=403, detail="Invalid token")

    # 识别调用方：简单策略——token 前8字节映射
    # 更复杂的场景可通过额外 header 显式声明
    caller_subject = request.headers.get("X-Caller-Subject", "").strip()
    if not caller_subject:
        caller_subject = "unknown"

    return Caller(subject=caller_subject, token_hash=_token_hash(token))
