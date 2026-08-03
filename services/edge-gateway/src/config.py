"""运行时配置。密钥从 Docker Secret 读取，支持 provider 热切换。"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class Config:
    gateway_port: int
    gateway_host: str
    log_level: str

    # Provider 配置
    default_provider: str  # "openai" | "anthropic" | "mock"
    openai_api_key: str
    openai_base_url: str
    openai_model: str
    anthropic_api_key: str
    anthropic_base_url: str
    anthropic_model: str

    # 可选：Postgres 配置（用于读取 edge_gateway_config 表做热切换）
    postgres_dsn: Optional[str] = None

    # 管理接口鉴权（空字符串表示禁用鉴权，仅用于测试/开发）
    admin_token: str = ""


def _read_secret(path: str) -> str:
    if not path or not os.path.isfile(path):
        return ""
    with open(path, "r", encoding="utf-8") as f:
        return f.read().rstrip("\n\r")


def load_config() -> Config:
    return Config(
        gateway_port=int(os.environ.get("GATEWAY_PORT", "8002")),
        gateway_host=os.environ.get("GATEWAY_HOST", "0.0.0.0"),
        log_level=os.environ.get("LOG_LEVEL", "INFO"),
        default_provider=os.environ.get("DEFAULT_PROVIDER", "mock"),
        openai_api_key=_read_secret(os.environ.get("OPENAI_API_KEY_FILE", "")),
        openai_base_url=os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1"),
        openai_model=os.environ.get("OPENAI_MODEL", "gpt-4o-mini"),
        anthropic_api_key=_read_secret(os.environ.get("ANTHROPIC_API_KEY_FILE", "")),
        anthropic_base_url=os.environ.get("ANTHROPIC_BASE_URL", "https://api.anthropic.com"),
        anthropic_model=os.environ.get("ANTHROPIC_MODEL", "claude-3-5-haiku-20241022"),
        postgres_dsn=os.environ.get("POSTGRES_DSN") or None,
        admin_token=_read_secret(os.environ.get("ADMIN_TOKEN_FILE", "")),
    )
