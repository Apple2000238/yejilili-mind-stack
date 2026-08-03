"""MCP 桥接：XinChao 协议 → Nocturne 协议适配

核心兼容缺口解决：
- breath(query, max_results, max_tokens) → trace() 或 breath()
- hold(content, tags, importance, auto, source) → hold() + provenance
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

from .auth import Caller
from .config import Config
from .nocturne_client import NocturneClient
from .provenance import ProvenanceStore

logger = logging.getLogger("adapter.mcp_bridge")


def _clamp(value: int, min_val: int, max_val: int, default: int) -> int:
    if value is None or not isinstance(value, int):
        return default
    return max(min_val, min(max_val, value))


def _extract_text(result: dict) -> str:
    """从 MCP tool result 中提取文本内容。"""
    content = (
        result.get("result", {}).get("content")
        or result.get("content")
        or []
    )
    if isinstance(content, list):
        return "\n".join(
            part.get("text", "") for part in content
            if isinstance(part, dict) and part.get("type") == "text"
        )
    return str(content)


def _event_id(caller: Caller, tool: str, normalized_input: dict) -> str:
    """基于调用方 + 工具 + 规范化输入生成幂等键。"""
    key = json.dumps({
        "caller": caller.subject,
        "tool": tool,
        "input": normalized_input,
    }, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(key.encode()).hexdigest()[:32]


class MCPBridge:
    """MCP 适配桥。"""

    def __init__(self, config: Config, nocturne: NocturneClient, provenance: ProvenanceStore):
        self.config = config
        self.nocturne = nocturne
        self.provenance = provenance

    # ─── breath 适配 ──────────────────────────────────────────────────────────

    async def handle_breath(
        self,
        caller: Caller,
        query: str | None = None,
        max_results: int | None = None,
        max_tokens: int | None = None,
    ) -> dict:
        """
        适配 XinChao 的 breath(query, max_results, max_tokens) 调用。

        路由规则：
        - 非空 query → 调用上游 trace(query, limit)
        - 空 query → 调用上游零参 breath()
        """
        query = (query or "").strip()
        req_max_results = _clamp(max_results, 1, self.config.breath_max_results_limit, self.config.breath_default_max_results)
        req_max_tokens = _clamp(max_tokens, 100, self.config.breath_max_tokens_limit, self.config.breath_default_max_tokens)

        route = "trace" if query else "breath"
        applied_max_results = req_max_results
        applied_max_tokens = req_max_tokens

        try:
            if query:
                # 有 query：调用 trace，limit = clamp(max_results, 1, 20)
                upstream_result = await self.nocturne.trace(query, limit=applied_max_results)
            else:
                # 无 query：调用零参 breath()
                upstream_result = await self.nocturne.breath()

            text = _extract_text(upstream_result)

            # token 截断（在 adapter 层施加 max_tokens 上限）
            truncated = False
            # 简单字符级截断；更精确的做法用 tiktoken
            if len(text) > applied_max_tokens:
                text = text[:applied_max_tokens]
                truncated = True

            # 构建确定性 metadata
            metadata = {
                "route": route,
                "query_honored": bool(query),
                "requested_max_results": req_max_results,
                "applied_max_results": applied_max_results,
                "requested_max_tokens": req_max_tokens,
                "applied_max_tokens": applied_max_tokens,
                "truncated": truncated,
                "upstream_snapshot": self.config.upstream_commit,
            }

            # 记录 provenance
            event_id = _event_id(caller, "breath", {
                "query": query,
                "max_results": max_results,
                "max_tokens": max_tokens,
            })
            self.provenance.record(
                event_id=event_id,
                tool_name="breath",
                caller_subject=caller.subject,
                auto=None,
                source=None,
                input_payload={"query": query, "max_results": max_results, "max_tokens": max_tokens},
                target_kind="nocturne",
                target_ref=None,
                result_payload={"text_length": len(text), "truncated": truncated},
                idempotency_status="new",
                metadata=metadata,
            )

            return {
                "content": [{"type": "text", "text": text}],
                "metadata": metadata,
            }

        except Exception as e:
            logger.error("breath adaptation failed: %s", e, exc_info=True)
            raise

    # ─── hold 适配 ────────────────────────────────────────────────────────────

    async def handle_hold(
        self,
        caller: Caller,
        content: str,
        tags: str = "",
        importance: int = 5,
        auto: bool | None = None,
        source: str | None = None,
    ) -> dict:
        """
        适配 XinChao 的 hold(content, tags, importance, auto, source) 调用。

        处理规则：
        1. 校验 auto 为 boolean，source 为受限标识（长度 ≤64）
        2. 将来源物化为 Nocturne tags：保留调用方 tags，附加 origin:xinchao、source:<source>、auto:true|false
        3. 以 event_id 做幂等键，重复请求返回第一次的 target ref
        4. 记录 adapter_provenance
        """
        content = (content or "").strip()
        if not content:
            raise ValueError("content is required")

        # 校验 source
        source = (source or "").strip()
        if source and len(source) > 64:
            raise ValueError("source must be ≤64 characters")
        if source and not all(c.isalnum() or c in "-_" for c in source):
            raise ValueError("source must be alphanumeric with hyphens/underscores only")

        # 构建增强 tags
        tag_parts = [t.strip() for t in (tags or "").split(",") if t.strip()]
        tag_parts.append("origin:xinchao")
        if source:
            tag_parts.append(f"source:{source}")
        if auto is not None:
            tag_parts.append(f"auto:{str(auto).lower()}")
        # 去重并保持稳定顺序
        seen = set()
        final_tags = []
        for t in tag_parts:
            if t.lower() not in seen:
                seen.add(t.lower())
                final_tags.append(t)
        tag_str = ",".join(final_tags)

        # 幂等键
        normalized_input = {
            "content": content,
            "tags": tags,
            "importance": importance,
            "auto": auto,
            "source": source,
        }
        event_id = _event_id(caller, "hold", normalized_input)

        # 检查幂等性
        existing = self.provenance.check_idempotency(event_id, normalized_input)
        if existing and existing.get("target_ref"):
            logger.info("hold idempotent hit: event_id=%s target_ref=%s", event_id, existing["target_ref"])
            return {
                "content": [{"type": "text", "text": f"idempotent: {existing['target_ref']}"}],
                "metadata": {"idempotent": True, "target_ref": existing["target_ref"]},
            }

        try:
            upstream_result = await self.nocturne.hold(
                content=content,
                kind="memory",
                tags=tag_str,
                importance=importance,
            )

            text = _extract_text(upstream_result)
            # 尝试从结果中提取 target ref（通常是 bucket id）
            target_ref = None
            import re
            m = re.search(r"[a-f0-9]{12,}", text)
            if m:
                target_ref = m.group(0)

            # 记录 provenance
            self.provenance.record(
                event_id=event_id,
                tool_name="hold",
                caller_subject=caller.subject,
                auto=auto,
                source=source,
                input_payload=normalized_input,
                target_kind="nocturne_bucket",
                target_ref=target_ref,
                result_payload={"text": text[:200]},
                idempotency_status="new",
                metadata={"tags": final_tags, "importance": importance},
            )

            return {
                "content": [{"type": "text", "text": text}],
                "metadata": {
                    "target_ref": target_ref,
                    "tags": final_tags,
                    "source": source,
                    "auto": auto,
                },
            }

        except Exception as e:
            # 记录失败 provenance
            self.provenance.record(
                event_id=event_id,
                tool_name="hold",
                caller_subject=caller.subject,
                auto=auto,
                source=source,
                input_payload=normalized_input,
                target_kind="nocturne_bucket",
                target_ref=None,
                result_payload=None,
                idempotency_status="failed",
                error=str(e)[:500],
            )
            logger.error("hold adaptation failed: %s", e, exc_info=True)
            raise
