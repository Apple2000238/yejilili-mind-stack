"""PromptPlan 注入模块

在转发给上游 LLM 前注入 PromptPlan，用于：
- 隔离 VPS 验收时的"我是谁/梨梨是谁/我们经历了什么"连续性测试
- 保护身份基岩（identity bedrock）不被截断
- 预算保护：总 prompt token 不超过阈值
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger("gateway.prompt_plan")

# ─── 默认 token 估算：英文 ~4 chars/token，中文 ~2 chars/token
# 这里使用保守估计
_CHARS_PER_TOKEN = 3.5


@dataclass
class PromptPlan:
    """PromptPlan 配置项"""

    # 身份基岩（最高优先级，不可截断）
    identity_bedrock: str = ""
    # 连续性上下文（中等优先级）
    continuity_context: str = ""
    # 系统指令（最低优先级，可被截断）
    system_instruction: str = ""
    # 预算上限（token 数）
    token_budget: int = 4000
    # 是否启用注入
    enabled: bool = True

    def estimate_tokens(self, text: str) -> int:
        """粗略估算 token 数。"""
        return max(1, int(len(text) / _CHARS_PER_TOKEN))

    def assemble(self, existing_messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """
        将 PromptPlan 注入到 messages 列表中。

        策略：
        1. 在所有 system messages 之前插入 identity_bedrock（最高优先级）
        2. 插入 continuity_context
        3. 保留原有的 system instruction（如果存在）
        4. 如果超出预算，只截断 system_instruction，不截断 identity_bedrock
        """
        if not self.enabled:
            return existing_messages

        # 分离出原有的 system message 和非 system message
        existing_system = []
        user_assistant_msgs = []
        for m in existing_messages:
            if m.get("role") == "system":
                existing_system.append(m)
            else:
                user_assistant_msgs.append(m)

        # 构建新的 system messages，按优先级排序
        new_system_messages: list[dict[str, Any]] = []

        # 1. identity_bedrock（不可截断）
        if self.identity_bedrock:
            new_system_messages.append({
                "role": "system",
                "content": self.identity_bedrock,
                "_prompt_plan": "identity_bedrock",
            })

        # 2. continuity_context（中等优先级）
        if self.continuity_context:
            new_system_messages.append({
                "role": "system",
                "content": self.continuity_context,
                "_prompt_plan": "continuity_context",
            })

        # 3. 系统指令（最低优先级，可被截断）
        if self.system_instruction:
            new_system_messages.append({
                "role": "system",
                "content": self.system_instruction,
                "_prompt_plan": "system_instruction",
            })

        # 4. 原有的 system messages
        new_system_messages.extend(existing_system)

        # 预算保护：计算当前总 token
        total_tokens = sum(self.estimate_tokens(m.get("content", "")) for m in new_system_messages)
        total_tokens += sum(self.estimate_tokens(m.get("content", "")) for m in user_assistant_msgs)

        if total_tokens > self.token_budget:
            # 超出预算：从最低优先级的 system_instruction 开始截断
            logger.warning(
                "prompt budget exceeded: %d > %d, truncating lowest-priority system prompts",
                total_tokens, self.token_budget,
            )
            new_system_messages = self._truncate_to_budget(new_system_messages)

        return new_system_messages + user_assistant_msgs

    def _truncate_to_budget(self, system_msgs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """截断低优先级 system messages 以符合预算，保护 identity_bedrock。"""
        result = []
        current_tokens = 0

        # 先计算非 system 的 token（这里只处理 system 列表，外部会再加）
        for m in system_msgs:
            tag = m.get("_prompt_plan", "")
            content = m.get("content", "")
            tokens = self.estimate_tokens(content)

            if tag == "identity_bedrock":
                # 身份基岩：永远保留
                result.append(m)
                current_tokens += tokens
            elif tag == "continuity_context":
                # 连续性上下文：尝试保留，如果超预算则截断
                if current_tokens + tokens <= self.token_budget:
                    result.append(m)
                    current_tokens += tokens
                else:
                    # 截断到剩余预算
                    remaining = self.token_budget - current_tokens
                    if remaining > 50:  # 至少保留 50 tokens 的上下文
                        truncated = self._truncate_text(content, remaining)
                        result.append({"role": "system", "content": truncated, "_prompt_plan": "continuity_context_truncated"})
                        current_tokens += self.estimate_tokens(truncated)
            elif tag == "system_instruction":
                # 系统指令：最低优先级，最容易被截断
                if current_tokens + tokens <= self.token_budget:
                    result.append(m)
                    current_tokens += tokens
                else:
                    remaining = self.token_budget - current_tokens
                    if remaining > 20:
                        truncated = self._truncate_text(content, remaining)
                        result.append({"role": "system", "content": truncated, "_prompt_plan": "system_instruction_truncated"})
                        current_tokens += self.estimate_tokens(truncated)
            else:
                # 原有 system message
                if current_tokens + tokens <= self.token_budget:
                    result.append(m)
                    current_tokens += tokens

        return result

    def _truncate_text(self, text: str, max_tokens: int) -> str:
        """按 token 预算截断文本（简单字符估算）。"""
        max_chars = int(max_tokens * _CHARS_PER_TOKEN)
        if len(text) <= max_chars:
            return text
        return text[:max_chars] + "\n...[truncated]"


def load_prompt_plan(config: dict[str, Any] | None = None) -> PromptPlan:
    """
    从配置加载 PromptPlan。

    支持从环境变量读取：
    - GATEWAY_PROMPT_IDENTITY_BEDROCK
    - GATEWAY_PROMPT_CONTINUITY_CONTEXT
    - GATEWAY_PROMPT_SYSTEM_INSTRUCTION
    - GATEWAY_PROMPT_TOKEN_BUDGET（默认 4000）
    """
    import os

    if config is None:
        config = {}

    def _get(key: str, default: str = "") -> str:
        # 优先从 config 字典，其次环境变量
        return config.get(key, os.environ.get(key, default))

    return PromptPlan(
        identity_bedrock=_get("GATEWAY_PROMPT_IDENTITY_BEDROCK"),
        continuity_context=_get("GATEWAY_PROMPT_CONTINUITY_CONTEXT"),
        system_instruction=_get("GATEWAY_PROMPT_SYSTEM_INSTRUCTION"),
        token_budget=int(_get("GATEWAY_PROMPT_TOKEN_BUDGET", "4000")),
        enabled=_get("GATEWAY_PROMPT_ENABLED", "true").lower() != "false",
    )
