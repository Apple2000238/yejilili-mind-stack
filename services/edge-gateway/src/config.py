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
    postgres_dsn: Optional[str] = None

    # 管理接口鉴权（空字符串表示禁用鉴权，仅用于测试/开发）
    admin_token: str = ""


def _read_secret(path: str) -> str:
    if not path or not os.path.isfile(path):
        return ""
    with open(path, "r", encoding="utf-8") as f:
        return f.read().rstrip("\n\r")


def load_config() -> Config:
    gateway_port = int(os.environ.get("GATEWAY_PORT", "8002"))
    gateway_host = os.environ.get("GATEWAY_HOST", "0.0.0.0")
    log_level = os.environ.get("LOG_LEVEL", "INFO")
    default_provider = os.environ.get("DEFAULT_PROVIDER", "mock")
    openai_api_key = _read_secret(os.environ.get("OPENAI_API_KEY_FILE", ""))
    openai_base_url = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")
    openai_model = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
    anthropic_api_key = _read_secret(os.environ.get("ANTHROPIC_API_KEY_FILE", ""))
    anthropic_base_url = os.environ.get("ANTHROPIC_BASE_URL", "https://api.anthropic.com")
    anthropic_model = os.environ.get("ANTHROPIC_MODEL", "claude-3-5-haiku-20241022")
    admin_token = _read_secret(os.environ.get("ADMIN_TOKEN_FILE", ""))

    # 优先直接 DSN，其次从组件构造（支持 Docker secret 密码文件）
    postgres_dsn = os.environ.get("POSTGRES_DSN") or None
    if not postgres_dsn:
        pg_host = os.environ.get("POSTGRES_HOST", "continuity-ledger")
        pg_port = os.environ.get("POSTGRES_PORT", "5432")
        pg_db = os.environ.get("POSTGRES_DB", "continuity_ledger")
        pg_user = os.environ.get("POSTGRES_USER", "continuity")
        pg_password_file = os.environ.get("POSTGRES_PASSWORD_FILE", "")
        pg_password = _read_secret(pg_password_file) if pg_password_file else ""
        if pg_password:
            postgres_dsn = f"postgresql://{pg_user}:{pg_password}@{pg_host}:{pg_port}/{pg_db}"

    return Config(
        gateway_port=gateway_port,
        gateway_host=gateway_host,
        log_level=log_level,
        default_provider=default_provider,
        openai_api_key=openai_api_key,
        openai_base_url=openai_base_url,
        openai_model=openai_model,
        anthropic_api_key=anthropic_api_key,
        anthropic_base_url=anthropic_base_url,
        anthropic_model=anthropic_model,
        postgres_dsn=postgres_dsn,
        admin_token=admin_token,
    )
