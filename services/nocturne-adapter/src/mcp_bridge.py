"""MCP 桥接：XinChao 协议 → Nocturne 协议适配

核心兼容缺口解决：
- breath(query, max_results, max_tokens) → trace() 或 breath()
- hold(content, tags, importance, auto, source) → hold() + provenance
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from typing import Any

from .auth import Caller
from .config import Config
from .nocturne_client import NocturneClient
from .provenance import ProvenanceStore

logger = logging.getLogger("adapter.mcp_bridge")

# ─── 允许值白名单 ─────────────────────────────────────────────────────────────
SOURCE_ALLOWLIST: set[str] = {
    "xinchao-dream",
    "xinchao-handoff",
    "xinchao-thought",
    "xinchao-heartbeat",
    "edge-gateway",
    "migration-cli",
}


# ─── 严格参数校验 ─────────────────────────────────────────────────────────────

def _validate_breath(
    query: Any,
    max_results: Any,
    max_tokens: Any,
    config: Config,
) -> tuple[str, int, int]:
    """
    严格校验 breath 参数。
    超限参数返回 ValueError（不静默 clamp）。
    """
    query_str = (query or "").strip() if isinstance(query, str) else ""

    if max_results is not None:
        if not isinstance(max_results, int) or isinstance(max_results, bool):
            raise ValueError("max_results must be an integer")
        if max_results < 1 or max_results > config.breath_max_results_limit:
            raise ValueError(
                f"max_results must be between 1 and {config.breath_max_results_limit}, "
                f"got {max_results}"
            )
    else:
        max_results = config.breath_default_max_results

    if max_tokens is not None:
        if not isinstance(max_tokens, int) or isinstance(max_tokens, bool):
            raise ValueError("max_tokens must be an integer")
        if max_tokens < 100 or max_tokens > config.breath_max_tokens_limit:
            raise ValueError(
                f"max_tokens must be between 100 and {config.breath_max_tokens_limit}, "
                f"got {max_tokens}"
            )
    else:
        max_tokens = config.breath_default_max_tokens

    return query_str, max_results, max_tokens


def _validate_hold(
    content: Any,
    tags: Any,
    importance: Any,
    auto: Any,
    source: Any,
) -> tuple[str, str, int, bool | None, str]:
    """
    严格校验 hold 参数。
    - content: 必填，非空字符串
    - importance: 1-10 整数
    - auto: 必须为 boolean，不可为 truthy/falsy 其他类型
    - source: 必须在允许值白名单中
    """
    if content is None or not isinstance(content, str) or not content.strip():
        raise ValueError("content is required and must be a non-empty string")
    content_str = content.strip()

    if tags is not None and not isinstance(tags, str):
        raise ValueError("tags must be a string")
    tags_str = (tags or "").strip()

    if importance is not None:
        if not isinstance(importance, int) or isinstance(importance, bool):
            raise ValueError("importance must be an integer")
        if importance < 1 or importance > 10:
            raise ValueError("importance must be between 1 and 10")
    else:
        importance = 5

    # auto 严格 boolean 校验：拒绝 int、str 等 truthy/falsy 值
    if auto is not None and not isinstance(auto, bool):
        raise ValueError("auto must be a boolean (true/false)")

    # source 白名单校验
    source_str = ""
    if source is not None:
        if not isinstance(source, str):
            raise ValueError("source must be a string")
        source_str = source.strip()
        if source_str:
            if len(source_str) > 64:
                raise ValueError("source must be ≤64 characters")
            if not all(c.isalnum() or c in "-_" for c in source_str):
                raise ValueError("source must be alphanumeric with hyphens/underscores only")
            if source_str not in SOURCE_ALLOWLIST:
                raise ValueError(
                    f"source '{source_str}' is not in allowlist. "
                    f"Allowed: {', '.join(sorted(SOURCE_ALLOWLIST))}"
                )

    return content_str, tags_str, importance, auto, source_str


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


# ─── MCP 适配桥 ───────────────────────────────────────────────────────────────

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
        query: Any = None,
        max_results: Any = None,
        max_tokens: Any = None,
    ) -> dict:
        """
        适配 XinChao 的 breath(query, max_results, max_tokens) 调用。

        路由规则：
        - 非空 query → 调用上游 trace(query, limit)
        - 空 query → 调用上游零参 breath()
        """
        query_str, req_max_results, req_max_tokens = _validate_breath(
            query, max_results, max_tokens, self.config
        )

        route = "trace" if query_str else "breath"

        try:
            if query_str:
                upstream_result = await self.nocturne.trace(query_str, limit=req_max_results)
            else:
                upstream_result = await self.nocturne.breath()

            text = _extract_text(upstream_result)

            # token 截断（字符级近似；精确做法需 tiktoken）
            truncated = False
            if len(text) > req_max_tokens:
                text = text[:req_max_tokens]
                truncated = True

            metadata = {
                "route": route,
                "query_honored": bool(query_str),
                "max_results": req_max_results,
                "max_tokens": req_max_tokens,
                "truncated": truncated,
                "upstream_snapshot": self.config.upstream_commit,
            }

            event_id = _event_id(caller, "breath", {
                "query": query_str,
                "max_results": req_max_results,
                "max_tokens": req_max_tokens,
            })
            self.provenance.record(
                event_id=event_id,
                tool_name="breath",
                caller_subject=caller.subject,
                auto=None,
                source=None,
                input_payload={"query": query_str, "max_results": req_max_results, "max_tokens": req_max_tokens},
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
        content: Any = None,
        tags: Any = None,
        importance: Any = None,
        auto: Any = None,
        source: Any = None,
    ) -> dict:
        """
        适配 XinChao 的 hold(content, tags, importance, auto, source) 调用。

        处理规则：
        1. 严格参数校验（见 _validate_hold）
        2. 将来源物化为 Nocturne tags
        3. 以 event_id + input_hash 做幂等键，先写账本再调用上游
        4. 记录 adapter_provenance
        """
        content_str, tags_str, importance_val, auto_val, source_str = _validate_hold(
            content, tags, importance, auto, source
        )

        # 构建增强 tags
        tag_parts = [t.strip() for t in tags_str.split(",") if t.strip()]
        tag_parts.append("origin:xinchao")
        if source_str:
            tag_parts.append(f"source:{source_str}")
        if auto_val is not None:
            tag_parts.append(f"auto:{str(auto_val).lower()}")
        seen = set()
        final_tags = []
        for t in tag_parts:
            if t.lower() not in seen:
                seen.add(t.lower())
                final_tags.append(t)
        tag_str = ",".join(final_tags)

        # 幂等键
        normalized_input = {
            "content": content_str,
            "tags": tags_str,
            "importance": importance_val,
            "auto": auto_val,
            "source": source_str,
        }
        event_id = _event_id(caller, "hold", normalized_input)

        # 检查幂等性（数据库层防并发）
        existing = self.provenance.check_idempotency(event_id, normalized_input)
        if existing and existing.get("target_ref"):
            logger.info("hold idempotent hit: event_id=%s target_ref=%s", event_id, existing["target_ref"])
            return {
                "content": [{"type": "text", "text": f"idempotent: {existing['target_ref']}"}],
                "metadata": {"idempotent": True, "target_ref": existing["target_ref"]},
            }

        try:
            upstream_result = await self.nocturne.hold(
                content=content_str,
                kind="memory",
                tags=tag_str,
                importance=importance_val,
            )

            text = _extract_text(upstream_result)
            # 尝试从结果中提取 target ref（bucket id）
            target_ref = None
            m = re.search(r"[a-f0-9]{12,}", text)
            if m:
                target_ref = m.group(0)

            self.provenance.record(
                event_id=event_id,
                tool_name="hold",
                caller_subject=caller.subject,
                auto=auto_val,
                source=source_str,
                input_payload=normalized_input,
                target_kind="nocturne_bucket",
                target_ref=target_ref,
                result_payload={"text": text[:200]},
                idempotency_status="new",
                metadata={"tags": final_tags, "importance": importance_val},
            )

            return {
                "content": [{"type": "text", "text": text}],
                "metadata": {
                    "target_ref": target_ref,
                    "tags": final_tags,
                    "source": source_str,
                    "auto": auto_val,
                },
            }

        except Exception as e:
            self.provenance.record(
                event_id=event_id,
                tool_name="hold",
                caller_subject=caller.subject,
                auto=auto_val,
                source=source_str,
                input_payload=normalized_input,
                target_kind="nocturne_bucket",
                target_ref=None,
                result_payload=None,
                idempotency_status="failed",
                error=str(e)[:500],
            )
            logger.error("hold adaptation failed: %s", e, exc_info=True)
            raise
