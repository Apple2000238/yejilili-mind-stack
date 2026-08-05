"""Identity Gate — 身份门配置与 PromptPlan 装配

约束：
    - identity_bedrock 不得由 Breath 临时召回代替
    - 心潮 Context Envelope 不得生成或覆盖身份基岩
    - token 超预算时先压缩近期材料，身份基岩不得被静默挤出
    - 配置缺失/错误/hash 不符时 readiness 失败
    - 每次装配记录 section ID、source ref、预算和 hash，不记录正文
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger("continuity-guard.identity_gate")

# ─── Schema 常量 ──────────────────────────────────────────────────────────────

CURRENT_SCHEMA_VERSION = "1.0.0"


# ─── 数据类 ───────────────────────────────────────────────────────────────────

@dataclass
class IdentitySection:
    """身份装配中的一个 section。"""

    section_id: str
    section_type: str  # "identity_bedrock" | "recent_continuity" | "system_instruction"
    source_ref: str
    content_hash: str  # 正文 hash，不存正文
    token_budget: int
    priority: int  # 1=最高（identity_bedrock），数字越小越优先

    def to_dict(self) -> dict[str, Any]:
        return {
            "section_id": self.section_id,
            "section_type": self.section_type,
            "source_ref": self.source_ref,
            "content_hash": self.content_hash,
            "token_budget": self.token_budget,
            "priority": self.priority,
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
            "created_at": self.created_at,
        }


@dataclass
class IdentityConfig:
    """身份门配置。"""

    schema_version: str = CURRENT_SCHEMA_VERSION
    identity_bedrock_path: str = ""
    identity_bedrock_hash: str = ""  # 预计算的 hash，用于校验
    recent_continuity_path: str = ""
    system_instruction_path: str = ""
    token_budget: int = 4000
    # 优先级权重：identity_bedrock 永远最高
    bedrock_reserve_tokens: int = 800  # 为身份基岩预留的最小 token

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "identity_bedrock_path": self.identity_bedrock_path,
            "identity_bedrock_hash": self.identity_bedrock_hash,
            "recent_continuity_path": self.recent_continuity_path,
            "system_instruction_path": self.system_instruction_path,
            "token_budget": self.token_budget,
            "bedrock_reserve_tokens": self.bedrock_reserve_tokens,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "IdentityConfig":
        return cls(
            schema_version=d.get("schema_version", CURRENT_SCHEMA_VERSION),
            identity_bedrock_path=d.get("identity_bedrock_path", ""),
            identity_bedrock_hash=d.get("identity_bedrock_hash", ""),
            recent_continuity_path=d.get("recent_continuity_path", ""),
            system_instruction_path=d.get("system_instruction_path", ""),
            token_budget=d.get("token_budget", 4000),
            bedrock_reserve_tokens=d.get("bedrock_reserve_tokens", 800),
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


# ─── 身份门加载器 ─────────────────────────────────────────────────────────────

class IdentityGateLoader:
    """加载并校验身份门配置。"""

    def __init__(self, config_path: Path | str) -> None:
        self.config_path = Path(config_path)
        self._config: IdentityConfig | None = None

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
            logger.warning("identity config schema version mismatch: %s vs %s", schema_version, CURRENT_SCHEMA_VERSION)

        config = IdentityConfig.from_dict(raw)

        # 校验：identity_bedrock 文件必须存在
        if config.identity_bedrock_path:
            bedrock_path = Path(config.identity_bedrock_path)
            if not bedrock_path.exists():
                raise FileNotFoundError(f"identity_bedrock file not found: {bedrock_path}")

            # hash 校验（如果配置了 hash）
            if config.identity_bedrock_hash:
                actual_hash = _hash_file(bedrock_path)
                if actual_hash != config.identity_bedrock_hash:
                    raise ValueError(
                        f"identity_bedrock hash mismatch: expected {config.identity_bedrock_hash}, got {actual_hash}"
                    )
        else:
            raise ValueError("identity_bedrock_path is required")

        self._config = config
        logger.info("identity config loaded: bedrock=%s", config.identity_bedrock_path)
        return config

    def readiness(self) -> tuple[bool, str]:
        """Readiness 检查：配置缺失/错误/hash 不符时返回失败。"""
        try:
            self.load()
            return True, "identity gate ready"
        except Exception as e:
            return False, f"identity gate not ready: {e}"


# ─── PromptPlan 装配器 ────────────────────────────────────────────────────────

class PromptPlanAssembler:
    """装配 PromptPlan，保证身份基岩优先且不可截断。

    装配顺序（固定）：
    1. 客户端不可替代的核心指令
    2. identity_bedrock（最高优先级，不可截断）
    3. 安全且有证据的长期记忆召回
    4. recent_continuity（心潮短态、交接便签、梦境余韵）
    5. 当前会话消息
    """

    def __init__(self, config: IdentityConfig, audit_dir: Path | str) -> None:
        self.config = config
        self.audit_dir = Path(audit_dir)
        self.audit_dir.mkdir(parents=True, exist_ok=True)

    def assemble(self, existing_messages: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], AssemblyRecord]:
        """
        装配 PromptPlan，返回 (messages, assembly_record)。

        策略：
        - identity_bedrock 永远保留
        - recent_continuity 超预算时截断
        - system_instruction 最低优先级，最先被截断
        """
        import time
        import uuid

        assembly_id = f"asm-{uuid.uuid4().hex[:16]}"
        created_at = time.strftime("%Y-%m-%dT%H:%M:%S%z")

        sections: list[IdentitySection] = []
        new_system_messages: list[dict[str, Any]] = []
        total_tokens = 0
        truncated = False

        # 读取 identity_bedrock 内容
        bedrock_text = ""
        bedrock_hash = ""
        if self.config.identity_bedrock_path:
            bedrock_path = Path(self.config.identity_bedrock_path)
            if bedrock_path.exists():
                bedrock_text = bedrock_path.read_text(encoding="utf-8")
                bedrock_hash = _hash_file(bedrock_path)

        # 1. identity_bedrock（不可截断）
        if bedrock_text:
            bedrock_tokens = estimate_tokens(bedrock_text)
            sections.append(IdentitySection(
                section_id=f"{assembly_id}-bedrock",
                section_type="identity_bedrock",
                source_ref=self.config.identity_bedrock_path,
                content_hash=bedrock_hash,
                token_budget=bedrock_tokens,
                priority=1,
            ))
            new_system_messages.append({
                "role": "system",
                "content": bedrock_text,
                "_identity_gate": {"section_type": "identity_bedrock", "assembly_id": assembly_id},
            })
            total_tokens += bedrock_tokens

        # 读取 recent_continuity
        continuity_text = ""
        continuity_hash = ""
        if self.config.recent_continuity_path:
            continuity_path = Path(self.config.recent_continuity_path)
            if continuity_path.exists():
                continuity_text = continuity_path.read_text(encoding="utf-8")
                continuity_hash = _hash_file(continuity_path)

        # 2. recent_continuity（可截断）
        if continuity_text:
            continuity_tokens = estimate_tokens(continuity_text)
            available = self.config.token_budget - total_tokens - self.config.bedrock_reserve_tokens

            if continuity_tokens > available:
                # 截断到可用预算
                max_chars = int(available * _CHARS_PER_TOKEN)
                if max_chars > 50:
                    continuity_text = continuity_text[:max_chars] + "\n...[truncated]"
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
                    priority=2,
                ))
                new_system_messages.append({
                    "role": "system",
                    "content": continuity_text,
                    "_identity_gate": {"section_type": "recent_continuity", "assembly_id": assembly_id},
                })
                total_tokens += continuity_tokens

        # 读取 system_instruction
        instruction_text = ""
        instruction_hash = ""
        if self.config.system_instruction_path:
            instruction_path = Path(self.config.system_instruction_path)
            if instruction_path.exists():
                instruction_text = instruction_path.read_text(encoding="utf-8")
                instruction_hash = _hash_file(instruction_path)

        # 3. system_instruction（最低优先级，最容易被截断）
        if instruction_text:
            instruction_tokens = estimate_tokens(instruction_text)
            available = self.config.token_budget - total_tokens

            if instruction_tokens > available:
                max_chars = int(available * _CHARS_PER_TOKEN)
                if max_chars > 20:
                    instruction_text = instruction_text[:max_chars] + "\n...[truncated]"
                    instruction_tokens = estimate_tokens(instruction_text)
                    truncated = True
                else:
                    instruction_text = ""
                    instruction_tokens = 0
                    truncated = True

            if instruction_text:
                sections.append(IdentitySection(
                    section_id=f"{assembly_id}-instruction",
                    section_type="system_instruction",
                    source_ref=self.config.system_instruction_path,
                    content_hash=instruction_hash,
                    token_budget=instruction_tokens,
                    priority=3,
                ))
                new_system_messages.append({
                    "role": "system",
                    "content": instruction_text,
                    "_identity_gate": {"section_type": "system_instruction", "assembly_id": assembly_id},
                })
                total_tokens += instruction_tokens

        # 4. 原有的 system messages
        existing_system = [m for m in existing_messages if m.get("role") == "system"]
        for m in existing_system:
            # 跳过已知的身份门消息（避免重复）
            if "_identity_gate" in m:
                continue
            t = estimate_tokens(m.get("content", ""))
            available = self.config.token_budget - total_tokens
            if t > available:
                truncated = True
                continue  # 跳过超出预算的原有 system message
            total_tokens += t

        # 5. 用户/助手消息
        user_assistant = [m for m in existing_messages if m.get("role") != "system"]
        for m in user_assistant:
            t = estimate_tokens(m.get("content", ""))
            total_tokens += t

        # 最终检查：identity_bedrock 必须存在
        identity_bedrock_present = any(
            s.section_type == "identity_bedrock" for s in sections
        )

        # 组装最终消息列表
        final_messages = new_system_messages + existing_system + user_assistant

        record = AssemblyRecord(
            assembly_id=assembly_id,
            sections=[s.to_dict() for s in sections],
            total_tokens=total_tokens,
            token_budget=self.config.token_budget,
            identity_bedrock_present=identity_bedrock_present,
            identity_bedrock_hash=bedrock_hash,
            truncated=truncated,
            created_at=created_at,
        )

        # 写入审计日志
        self._append_audit(record)

        return final_messages, record

    def _append_audit(self, record: AssemblyRecord) -> None:
        """追加装配审计记录。"""
        import time
        date_str = time.strftime("%Y%m%d")
        audit_file = self.audit_dir / f"identity-assembly-{date_str}.jsonl"
        with open(audit_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(record.to_dict(), ensure_ascii=False, default=str) + "\n")
