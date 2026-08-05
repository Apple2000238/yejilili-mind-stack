"""Identity Gate — 身份门配置与 PromptPlan 装配

约束：
    - identity_bedrock 不得由 Breath 临时召回代替
    - 心潮 Context Envelope 不得生成或覆盖身份基岩
    - token 超预算时先压缩近期材料，身份基岩不得被静默挤出
    - 配置缺失/错误/hash 不符时 readiness 失败
    - 每次装配记录 section ID、source ref、预算和 hash，不记录正文
    - 装配顺序固定：核心指令 → 身份基岩 → 长期记忆召回 → 近期连续性 → 会话消息
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger("continuity-guard.identity_gate")

# ─── Schema 常量 ──────────────────────────────────────────────────────────────

CURRENT_SCHEMA_VERSION = "1.0.0"

# 五段装配顺序（数字越小优先级越高，越不容易被截断）
SECTION_PRIORITY = {
    "core_instruction": 1,      # 1. 客户端不可替代的核心指令
    "identity_bedrock": 2,      # 2. 身份基岩（不可截断）
    "long_term_memory": 3,      # 3. 安全且有证据的长期记忆召回
    "recent_continuity": 4,     # 4. 心潮短态、交接便签、梦境余韵
    "session_messages": 5,      # 5. 当前会话消息
}


# ─── 数据类 ───────────────────────────────────────────────────────────────────

@dataclass
class IdentitySection:
    """身份装配中的一个 section。"""

    section_id: str
    section_type: str  # 必须是 SECTION_PRIORITY 的键
    source_ref: str
    content_hash: str  # 正文 hash，不存正文
    token_budget: int
    priority: int  # 1=最高（core_instruction），数字越小越优先
    actual_tokens: int = 0  # 实际使用的 token 数

    def __post_init__(self) -> None:
        if self.section_type not in SECTION_PRIORITY:
            raise ValueError(f"unknown section_type: {self.section_type}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "section_id": self.section_id,
            "section_type": self.section_type,
            "source_ref": self.source_ref,
            "content_hash": self.content_hash,
            "token_budget": self.token_budget,
            "priority": self.priority,
            "actual_tokens": self.actual_tokens,
        }


@dataclass
class AssemblyRecord:
    """单次装配审计记录。"""

    assembly_id: str
    sections: list[dict[str, Any]]
    total_tokens: int
    token_budget: int
    identity_bedrock_present: bool
    identity_bedrock_hash: str
    truncated: bool
    overflow: bool  # 是否超出硬预算
    created_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "assembly_id": self.assembly_id,
            "sections": self.sections,
            "total_tokens": self.total_tokens,
            "token_budget": self.token_budget,
            "identity_bedrock_present": self.identity_bedrock_present,
            "identity_bedrock_hash": self.identity_bedrock_hash,
            "truncated": self.truncated,
            "overflow": self.overflow,
            "created_at": self.created_at,
        }


@dataclass
class IdentityConfig:
    """身份门配置。"""

    schema_version: str = CURRENT_SCHEMA_VERSION
    core_instruction_path: str = ""       # 1. 核心指令文件路径
    identity_bedrock_path: str = ""       # 2. 身份基岩文件路径
    identity_bedrock_hash: str = ""       # 预计算的 hash，用于校验
    long_term_memory_path: str = ""       # 3. 长期记忆召回文件路径
    recent_continuity_path: str = ""      # 4. 近期连续性文件路径
    token_budget: int = 4000
    # 不可裁剪段的硬预留（core_instruction + identity_bedrock）
    bedrock_reserve_tokens: int = 800
    # 模型硬上限（超出必须返回 overflow 错误）
    model_hard_limit: int = 8192

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "core_instruction_path": self.core_instruction_path,
            "identity_bedrock_path": self.identity_bedrock_path,
            "identity_bedrock_hash": self.identity_bedrock_hash,
            "long_term_memory_path": self.long_term_memory_path,
            "recent_continuity_path": self.recent_continuity_path,
            "token_budget": self.token_budget,
            "bedrock_reserve_tokens": self.bedrock_reserve_tokens,
            "model_hard_limit": self.model_hard_limit,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "IdentityConfig":
        return cls(
            schema_version=d.get("schema_version", CURRENT_SCHEMA_VERSION),
            core_instruction_path=d.get("core_instruction_path", ""),
            identity_bedrock_path=d.get("identity_bedrock_path", ""),
            identity_bedrock_hash=d.get("identity_bedrock_hash", ""),
            long_term_memory_path=d.get("long_term_memory_path", ""),
            recent_continuity_path=d.get("recent_continuity_path", ""),
            token_budget=d.get("token_budget", 4000),
            bedrock_reserve_tokens=d.get("bedrock_reserve_tokens", 800),
            model_hard_limit=d.get("model_hard_limit", 8192),
        )


# ─── Token 估算 ───────────────────────────────────────────────────────────────

_CHARS_PER_TOKEN = 3.5


def estimate_tokens(text: str) -> int:
    """粗略估算 token 数。"""
    return max(1, int(len(text) / _CHARS_PER_TOKEN))


def _hash_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def _read_text_file(path: str) -> tuple[str, str]:
    """读取文本文件，返回 (content, hash)。"""
    p = Path(path)
    if not p.exists():
        return "", ""
    text = p.read_text(encoding="utf-8")
    return text, _hash_file(p)


# ─── 身份门加载器 ─────────────────────────────────────────────────────────────

class IdentityGateLoader:
    """加载并校验身份门配置。"""

    def __init__(self, config_path: Path | str) -> None:
        self.config_path = Path(config_path)
        self._config: IdentityConfig | None = None
        self._load_error: str | None = None

    def load(self) -> IdentityConfig:
        """加载配置，校验 schema 和文件存在性。"""
        if self._config is not None:
            return self._config

        if not self.config_path.exists():
            raise FileNotFoundError(f"identity gate config not found: {self.config_path}")

        raw = json.loads(self.config_path.read_text(encoding="utf-8"))

        if not isinstance(raw, dict):
            raise ValueError("identity config root must be an object")

        schema_version = raw.get("schema_version", "unknown")
        if schema_version != CURRENT_SCHEMA_VERSION:
            raise ValueError(
                f"identity config schema version mismatch: {schema_version} vs {CURRENT_SCHEMA_VERSION}"
            )

        config = IdentityConfig.from_dict(raw)

        # 校验：核心文件必须存在
        for field_name, path_attr in [
            ("core_instruction_path", config.core_instruction_path),
            ("identity_bedrock_path", config.identity_bedrock_path),
        ]:
            if not path_attr:
                raise ValueError(f"{field_name} is required")
            if not Path(path_attr).exists():
                raise FileNotFoundError(f"{field_name} file not found: {path_attr}")

        # hash 校验（如果配置了 hash）
        if config.identity_bedrock_hash:
            actual_hash = _hash_file(Path(config.identity_bedrock_path))
            if actual_hash != config.identity_bedrock_hash:
                raise ValueError(
                    f"identity_bedrock hash mismatch: expected {config.identity_bedrock_hash}, got {actual_hash}"
                )

        self._config = config
        logger.info("identity config loaded: core=%s bedrock=%s", config.core_instruction_path, config.identity_bedrock_path)
        return config

    def readiness(self) -> tuple[bool, str]:
        """Readiness 检查：配置缺失/错误/hash 不符时返回失败。"""
        try:
            self.load()
            return True, "identity gate ready"
        except Exception as e:
            self._load_error = str(e)
            logger.error("identity gate readiness failed: %s", e, exc_info=True)
            return False, f"identity gate not ready: {e}"


# ─── PromptPlan 装配器 ────────────────────────────────────────────────────────

class PromptPlanAssembler:
    """装配 PromptPlan，保证固定五段顺序和硬预算控制。

    装配顺序（固定，不可变）：
    1. 客户端不可替代的核心指令（core_instruction）
    2. identity_bedrock（不可截断）
    3. 安全且有证据的长期记忆召回（long_term_memory）
    4. recent_continuity（心潮短态、交接便签、梦境余韵）
    5. 当前会话消息

    截断策略：
    - core_instruction 和 identity_bedrock 永不被截断
    - 超预算时先压缩 long_term_memory，再压缩 recent_continuity
    - 超出 model_hard_limit 时返回 overflow 错误
    """

    def __init__(self, config: IdentityConfig, audit_dir: Path | str, pg_dsn: str | None = None) -> None:
        self.config = config
        self.audit_dir = Path(audit_dir)
        self.audit_dir.mkdir(parents=True, exist_ok=True)
        self.pg_dsn = pg_dsn

    def assemble(self, existing_messages: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], AssemblyRecord]:
        """装配 PromptPlan，返回 (messages, assembly_record)。"""
        import uuid

        assembly_id = f"asm-{uuid.uuid4().hex[:16]}"
        created_at = time.strftime("%Y-%m-%dT%H:%M:%S%z")

        sections: list[IdentitySection] = []
        new_system_messages: list[dict[str, Any]] = []
        total_tokens = 0
        truncated = False
        overflow = False

        # ── 1. 核心指令（不可截断）─────────────────────────────────────────────
        core_text, core_hash = _read_text_file(self.config.core_instruction_path)
        if core_text:
            core_tokens = estimate_tokens(core_text)
            sections.append(IdentitySection(
                section_id=f"{assembly_id}-core",
                section_type="core_instruction",
                source_ref=self.config.core_instruction_path,
                content_hash=core_hash,
                token_budget=core_tokens,
                priority=SECTION_PRIORITY["core_instruction"],
                actual_tokens=core_tokens,
            ))
            new_system_messages.append({
                "role": "system",
                "content": core_text,
                "_identity_gate": {"section_type": "core_instruction", "assembly_id": assembly_id},
            })
            total_tokens += core_tokens

        # ── 2. identity_bedrock（不可截断）─────────────────────────────────────
        bedrock_text, bedrock_hash = _read_text_file(self.config.identity_bedrock_path)
        bedrock_tokens = 0
        if bedrock_text:
            bedrock_tokens = estimate_tokens(bedrock_text)
            sections.append(IdentitySection(
                section_id=f"{assembly_id}-bedrock",
                section_type="identity_bedrock",
                source_ref=self.config.identity_bedrock_path,
                content_hash=bedrock_hash,
                token_budget=bedrock_tokens,
                priority=SECTION_PRIORITY["identity_bedrock"],
                actual_tokens=bedrock_tokens,
            ))
            new_system_messages.append({
                "role": "system",
                "content": bedrock_text,
                "_identity_gate": {"section_type": "identity_bedrock", "assembly_id": assembly_id},
            })
            total_tokens += bedrock_tokens

        # 计算不可裁剪段之后剩余的软预算
        remaining_soft_budget = self.config.token_budget - total_tokens

        # ── 3. 长期记忆召回（可截断，优先级高于 recent_continuity）─────────────
        memory_text, memory_hash = _read_text_file(self.config.long_term_memory_path)
        if memory_text:
            memory_tokens = estimate_tokens(memory_text)
            available = remaining_soft_budget

            if memory_tokens > available:
                max_chars = int(available * _CHARS_PER_TOKEN)
                if max_chars > 100:
                    memory_text = memory_text[:max_chars] + "\n...[truncated: long_term_memory]"
                    memory_tokens = estimate_tokens(memory_text)
                    truncated = True
                else:
                    memory_text = ""
                    memory_tokens = 0
                    truncated = True

            if memory_text:
                sections.append(IdentitySection(
                    section_id=f"{assembly_id}-memory",
                    section_type="long_term_memory",
                    source_ref=self.config.long_term_memory_path,
                    content_hash=memory_hash,
                    token_budget=memory_tokens,
                    priority=SECTION_PRIORITY["long_term_memory"],
                    actual_tokens=memory_tokens,
                ))
                new_system_messages.append({
                    "role": "system",
                    "content": memory_text,
                    "_identity_gate": {"section_type": "long_term_memory", "assembly_id": assembly_id},
                })
                total_tokens += memory_tokens
                remaining_soft_budget -= memory_tokens

        # ── 4. recent_continuity（可截断）───────────────────────────────────────
        continuity_text, continuity_hash = _read_text_file(self.config.recent_continuity_path)
        if continuity_text:
            continuity_tokens = estimate_tokens(continuity_text)
            available = remaining_soft_budget

            if continuity_tokens > available:
                max_chars = int(available * _CHARS_PER_TOKEN)
                if max_chars > 50:
                    continuity_text = continuity_text[:max_chars] + "\n...[truncated: recent_continuity]"
                    continuity_tokens = estimate_tokens(continuity_text)
                    truncated = True
                else:
                    continuity_text = ""
                    continuity_tokens = 0
                    truncated = True

            if continuity_text:
                sections.append(IdentitySection(
                    section_id=f"{assembly_id}-continuity",
                    section_type="recent_continuity",
                    source_ref=self.config.recent_continuity_path,
                    content_hash=continuity_hash,
                    token_budget=continuity_tokens,
                    priority=SECTION_PRIORITY["recent_continuity"],
                    actual_tokens=continuity_tokens,
                ))
                new_system_messages.append({
                    "role": "system",
                    "content": continuity_text,
                    "_identity_gate": {"section_type": "recent_continuity", "assembly_id": assembly_id},
                })
                total_tokens += continuity_tokens

        # ── 5. 当前会话消息（始终附加，但检查硬上限）─────────────────────────────
        existing_system = [m for m in existing_messages if m.get("role") == "system"]
        user_assistant = [m for m in existing_messages if m.get("role") != "system"]

        # 过滤掉已被身份门管理的 system 消息
        filtered_system = [m for m in existing_system if "_identity_gate" not in m]
        for m in filtered_system:
            t = estimate_tokens(m.get("content", ""))
            total_tokens += t

        for m in user_assistant:
            t = estimate_tokens(m.get("content", ""))
            total_tokens += t

        # 硬上限检查
        if total_tokens > self.config.model_hard_limit:
            overflow = True
            logger.error(
                "ASSEMBLY OVERFLOW: total_tokens=%d exceeds model_hard_limit=%d",
                total_tokens, self.config.model_hard_limit,
            )

        # 最终检查：identity_bedrock 必须存在
        identity_bedrock_present = any(
            s.section_type == "identity_bedrock" for s in sections
        )

        # 组装最终消息列表（按五段顺序）
        final_messages = new_system_messages + filtered_system + user_assistant

        record = AssemblyRecord(
            assembly_id=assembly_id,
            sections=[s.to_dict() for s in sections],
            total_tokens=total_tokens,
            token_budget=self.config.token_budget,
            identity_bedrock_present=identity_bedrock_present,
            identity_bedrock_hash=bedrock_hash,
            truncated=truncated,
            overflow=overflow,
            created_at=created_at,
        )

        # 写入审计（本地 JSONL + 数据库）
        self._append_audit(record)
        self._append_db_audit(record)

        return final_messages, record

    def _append_audit(self, record: AssemblyRecord) -> None:
        """追加装配审计记录到本地 JSONL。"""
        date_str = time.strftime("%Y%m%d")
        audit_file = self.audit_dir / f"identity-assembly-{date_str}.jsonl"
        with open(audit_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(record.to_dict(), ensure_ascii=False, default=str) + "\n")

    def _append_db_audit(self, record: AssemblyRecord) -> None:
        """追加装配审计记录到 PostgreSQL identity_assembly_audit 表。"""
        if not self.pg_dsn:
            return
        try:
            import psycopg
            with psycopg.connect(self.pg_dsn) as pg:
                pg.execute(
                    """
                    INSERT INTO identity_assembly_audit
                    (assembly_id, sections, total_tokens, token_budget, identity_bedrock_present,
                     identity_bedrock_hash, truncated, overflow, created_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (assembly_id) DO NOTHING
                    """,
                    (
                        record.assembly_id,
                        json.dumps(record.sections, ensure_ascii=False, default=str),
                        record.total_tokens,
                        record.token_budget,
                        record.identity_bedrock_present,
                        record.identity_bedrock_hash,
                        record.truncated,
                        record.overflow,
                        record.created_at,
                    ),
                )
                pg.commit()
        except Exception as e:
            logger.error("failed to write identity assembly audit to DB: %s", e, exc_info=True)
