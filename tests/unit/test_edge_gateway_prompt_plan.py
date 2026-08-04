"""Unit tests: edge-gateway prompt_plan module

运行方式:
    pytest tests/unit/test_edge_gateway_prompt_plan.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "services" / "edge-gateway"))

import pytest

from src.prompt_plan import PromptPlan, load_prompt_plan


class TestPromptPlanAssemble:
    def test_disabled_returns_original(self):
        plan = PromptPlan(enabled=False, identity_bedrock="i am test")
        msgs = [{"role": "user", "content": "hello"}]
        result = plan.assemble(msgs)
        assert result == msgs

    def test_injects_identity_bedrock(self):
        plan = PromptPlan(identity_bedrock="You are Pear's AI.")
        msgs = [{"role": "user", "content": "hello"}]
        result = plan.assemble(msgs)
        assert result[0]["role"] == "system"
        assert result[0]["content"] == "You are Pear's AI."
        assert result[1] == msgs[0]

    def test_injects_all_three_layers(self):
        plan = PromptPlan(
            identity_bedrock="ID",
            continuity_context="CC",
            system_instruction="SI",
        )
        msgs = [{"role": "user", "content": "hi"}]
        result = plan.assemble(msgs)
        assert len(result) == 4
        assert result[0]["content"] == "ID"
        assert result[1]["content"] == "CC"
        assert result[2]["content"] == "SI"
        assert result[0]["_prompt_plan"] == "identity_bedrock"
        assert result[1]["_prompt_plan"] == "continuity_context"
        assert result[2]["_prompt_plan"] == "system_instruction"

    def test_preserves_existing_system_messages(self):
        plan = PromptPlan(identity_bedrock="IB")
        msgs = [
            {"role": "system", "content": "existing"},
            {"role": "user", "content": "hi"},
        ]
        result = plan.assemble(msgs)
        # identity_bedrock 应该在所有原有 system 之前
        assert result[0]["content"] == "IB"
        assert result[1]["content"] == "existing"
        assert result[2]["content"] == "hi"

    def test_truncate_low_priority_on_budget_exceeded(self):
        # 构造一个会超预算的 plan
        long_text = "x" * 20000  # ~5714 tokens (按 3.5 chars/token)
        plan = PromptPlan(
            identity_bedrock="KEEP",  # ~1 token
            continuity_context=long_text,
            system_instruction=long_text,
            token_budget=100,
        )
        msgs = [{"role": "user", "content": "hi"}]
        result = plan.assemble(msgs)
        # identity_bedrock 必须保留
        assert result[0]["content"] == "KEEP"
        # 其他 system messages 应该被截断或移除
        system_contents = [m["content"] for m in result if m.get("role") == "system"]
        assert "KEEP" in system_contents

    def test_identity_bedrock_never_truncated(self):
        long_identity = "x" * 10000
        plan = PromptPlan(
            identity_bedrock=long_identity,
            token_budget=50,
        )
        msgs = [{"role": "user", "content": "hi"}]
        result = plan.assemble(msgs)
        # 即使 identity 很长，它仍然保留（预算保护不截断 identity）
        assert result[0]["content"] == long_identity


class TestPromptPlanEstimateTokens:
    def test_empty_string(self):
        plan = PromptPlan()
        assert plan.estimate_tokens("") == 1

    def test_short_text(self):
        plan = PromptPlan()
        # 35 chars / 3.5 = 10 tokens
        assert plan.estimate_tokens("a" * 35) == 10


class TestLoadPromptPlan:
    def test_from_config_dict(self):
        config = {
            "GATEWAY_PROMPT_IDENTITY_BEDROCK": "identity",
            "GATEWAY_PROMPT_CONTINUITY_CONTEXT": "continuity",
            "GATEWAY_PROMPT_TOKEN_BUDGET": "2000",
        }
        plan = load_prompt_plan(config)
        assert plan.identity_bedrock == "identity"
        assert plan.continuity_context == "continuity"
        assert plan.token_budget == 2000

    def test_defaults(self):
        plan = load_prompt_plan({})
        assert plan.identity_bedrock == ""
        assert plan.token_budget == 4000
        assert plan.enabled is True

    def test_disabled_via_env_string(self):
        import os
        os.environ["GATEWAY_PROMPT_ENABLED"] = "false"
        plan = load_prompt_plan()
        assert plan.enabled is False
        del os.environ["GATEWAY_PROMPT_ENABLED"]
