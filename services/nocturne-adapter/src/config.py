"""运行时配置。所有密钥从 Docker Secret 文件读取，不硬编码。"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    adapter_port: int
    adapter_host: str
    nocturne_url: str
    mcp_adapter_token: str
    postgres_host: str
    postgres_port: int
    postgres_db: str
    postgres_user: str
    postgres_password: str
    log_level: str

    # 契约常量
    breath_max_results_limit: int = 20
    breath_max_tokens_limit: int = 4000
    breath_default_max_results: int = 12
    breath_default_max_tokens: int = 2200
    upstream_commit: str = "8fecd3b"


def _read_secret(path: str) -> str:
    """从 Docker Secret 文件读取值，去除末尾换行。"""
    if not path or not os.path.isfile(path):
        return ""
    with open(path, "r", encoding="utf-8") as f:
        return f.read().rstrip("\n\r")


def load_config() -> Config:
    """从环境变量和 Docker Secret 加载配置。"""
    return Config(
        adapter_port=int(os.environ.get("ADAPTER_PORT", "8001")),
        adapter_host=os.environ.get("ADAPTER_HOST", "0.0.0.0"),  # nosec: B104 - Docker container default bind
        nocturne_url=os.environ.get("NOCTURNE_URL", "http://nocturne:8000"),
        mcp_adapter_token=_read_secret(os.environ.get("MCP_ADAPTER_TOKEN_FILE", "")),
        postgres_host=os.environ.get("POSTGRES_HOST", "continuity-ledger"),
        postgres_port=int(os.environ.get("POSTGRES_PORT", "5432")),
        postgres_db=os.environ.get("POSTGRES_DB", "continuity_ledger"),
        postgres_user=os.environ.get("POSTGRES_USER", "continuity"),
        postgres_password=_read_secret(os.environ.get("POSTGRES_PASSWORD_FILE", "")),
        log_level=os.environ.get("LOG_LEVEL", "INFO"),
    )
