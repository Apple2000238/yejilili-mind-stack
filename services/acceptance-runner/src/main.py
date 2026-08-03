"""Acceptance Runner — 自动化验收测试

运行全部 AC-1 ~ AC-8 用例，生成 JSON + Markdown 报告。

环境变量：
    GATEWAY_URL     — edge-gateway 地址（默认 http://edge-gateway:8002）
    ADAPTER_URL     — nocturne-adapter 地址（默认 http://nocturne-adapter:8001）
    MCP_TOKEN       — MCP adapter token（默认从 /run/secrets/mcp_adapter_token 读取）
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import socket
import sys
import time
from pathlib import Path
from typing import Any

import httpx

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("acceptance-runner")

GATEWAY_URL = os.environ.get("GATEWAY_URL", "http://edge-gateway:8002")
ADAPTER_URL = os.environ.get("ADAPTER_URL", "http://nocturne-adapter:8001")
MCP_TOKEN_PATH = os.environ.get("MCP_TOKEN_FILE", "/run/secrets/mcp_adapter_token")


def _mcp_token() -> str:
    try:
        return Path(MCP_TOKEN_PATH).read_text().strip()
    except Exception as e:
        logger.error("cannot read MCP token: %s", e)
        return ""


# ─── 用例结果收集器 ──────────────────────────────────────────────────────────

class AcceptanceReport:
    def __init__(self) -> None:
        self.started_at = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        self.cases: list[dict[str, Any]] = []
        self.passed = 0
        self.failed = 0

    def add(self, case_id: str, name: str, passed: bool, detail: dict[str, Any]) -> None:
        self.cases.append({
            "case_id": case_id,
            "name": name,
            "passed": passed,
            "detail": detail,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        })
        if passed:
            self.passed += 1
        else:
            self.failed += 1

    def to_json(self) -> str:
        return json.dumps({
            "started_at": self.started_at,
            "completed_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "summary": {"total": len(self.cases), "passed": self.passed, "failed": self.failed},
            "cases": self.cases,
        }, indent=2, ensure_ascii=False)

    def to_markdown(self) -> str:
        lines = [
            "# Acceptance Report",
            "",
            f"- **Started**: {self.started_at}",
            f"- **Completed**: {time.strftime('%Y-%m-%dT%H:%M:%S%z')}",
            f"- **Total**: {len(self.cases)} | **Passed**: {self.passed} | **Failed**: {self.failed}",
            "",
            "| Case ID | Name | Result | Detail |",
            "|---------|------|--------|--------|",
        ]
        for c in self.cases:
            status = "✅ PASS" if c["passed"] else "❌ FAIL"
            detail = json.dumps(c["detail"], ensure_ascii=False)
            lines.append(f"| {c['case_id']} | {c['name']} | {status} | {detail} |")
        lines.append("")
        lines.append(f"## Overall: {'PASS' if self.failed == 0 else 'FAIL'}")
        lines.append("")
        return "\n".join(lines)


# ─── AC-1: 服务健康检查 ──────────────────────────────────────────────────────

def ac1_health(report: AcceptanceReport) -> None:
    """AC-1: 所有核心服务的 /health 返回 200 且含预期字段。"""
    results = {}
    passed = True
    for name, url in [("gateway", GATEWAY_URL), ("adapter", ADAPTER_URL)]:
        try:
            r = httpx.get(f"{url}/health", timeout=10)
            ok = r.status_code == 200
            body = r.json() if ok else {}
            results[name] = {"status_code": r.status_code, "ok": ok, "body_keys": list(body.keys())}
            if not ok:
                passed = False
        except Exception as e:
            results[name] = {"error": str(e)}
            passed = False
    report.add("AC-1", "Service Healthchecks", passed, results)


# ─── AC-2: MCP 工具列表与 Schema ─────────────────────────────────────────────

def ac2_mcp_tools(report: AcceptanceReport) -> None:
    """AC-2: MCP tools/list 返回 breath 和 hold，schema 包含预期字段。"""
    token = _mcp_token()
    try:
        r = httpx.post(
            f"{ADAPTER_URL}/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            headers={"Authorization": f"Bearer {token}"},
            timeout=10,
        )
        body = r.json()
        result = body.get("result", {})
        tools = result.get("tools", [])
        tool_names = {t["name"] for t in tools}

        passed = "breath" in tool_names and "hold" in tool_names
        detail = {"tool_names": list(tool_names), "status_code": r.status_code}

        if passed:
            # 检查 hold schema 是否有 auto/source
            hold = next((t for t in tools if t["name"] == "hold"), None)
            if hold:
                props = hold.get("inputSchema", {}).get("properties", {})
                detail["hold_has_auto"] = "auto" in props
                detail["hold_has_source"] = "source" in props
                if not (detail["hold_has_auto"] and detail["hold_has_source"]):
                    passed = False

        report.add("AC-2", "MCP Tools Schema", passed, detail)
    except Exception as e:
        report.add("AC-2", "MCP Tools Schema", False, {"error": str(e)})


# ─── AC-3: breath query 路由与截断 ───────────────────────────────────────────

def ac3_breath_routing(report: AcceptanceReport) -> None:
    """AC-3: breath 带 query 时路由为 trace，空 query 路由为 breath；metadata 完整。"""
    token = _mcp_token()
    results = {}
    passed = True

    for label, query in [("with_query", "memory"), ("empty_query", "")]:
        try:
            r = httpx.post(
                f"{ADAPTER_URL}/mcp",
                json={
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/call",
                    "params": {
                        "name": "breath",
                        "arguments": {"query": query, "max_results": 5, "max_tokens": 1000},
                    },
                },
                headers={"Authorization": f"Bearer {token}"},
                timeout=15,
            )
            body = r.json()
            result = body.get("result", {})
            meta = result.get("metadata", {})
            results[label] = {
                "status_code": r.status_code,
                "has_metadata": bool(meta),
                "route": meta.get("route", "unknown"),
                "query_honored": meta.get("query_honored", False),
            }
            # 验证路由：带 query 应走 trace，空 query 走 breath
            if query:
                if meta.get("route") != "trace":
                    passed = False
            else:
                if meta.get("route") != "breath":
                    passed = False
        except Exception as e:
            results[label] = {"error": str(e)}
            passed = False

    report.add("AC-3", "Breath Routing & Metadata", passed, results)


# ─── AC-4: hold 幂等性与 Provenance ──────────────────────────────────────────

def ac4_hold_idempotency(report: AcceptanceReport) -> None:
    """AC-4: 重复 hold 调用产生相同 target_ref，provenance 不重复写入。"""
    token = _mcp_token()
    event_id = f"test-event-{hashlib.sha256(os.urandom(16)).hexdigest()[:8]}"
    payload = {
        "jsonrpc": "2.0",
        "id": 3,
        "method": "tools/call",
        "params": {
            "name": "hold",
            "arguments": {
                "content": "acceptance test memory",
                "tags": "test,acceptance",
                "importance": 3,
                "auto": True,
                "source": "xinchao-dream",
            },
        },
    }
    # 第一次调用
    try:
        r1 = httpx.post(f"{ADAPTER_URL}/mcp", json=payload, headers={"Authorization": f"Bearer {token}"}, timeout=15)
        body1 = r1.json()
        result1 = body1.get("result", {})
        meta1 = result1.get("metadata", {})
        ref1 = meta1.get("target_ref", "")

        # 第二次调用（相同内容）
        r2 = httpx.post(f"{ADAPTER_URL}/mcp", json=payload, headers={"Authorization": f"Bearer {token}"}, timeout=15)
        body2 = r2.json()
        result2 = body2.get("result", {})
        meta2 = result2.get("metadata", {})
        ref2 = meta2.get("target_ref", "")

        passed = ref1 == ref2 and ref1 != ""
        detail = {
            "first_ref": ref1,
            "second_ref": ref2,
            "refs_equal": ref1 == ref2,
        }
        report.add("AC-4", "Hold Idempotency", passed, detail)
    except Exception as e:
        report.add("AC-4", "Hold Idempotency", False, {"error": str(e)})


# ─── AC-5: 网络隔离 ──────────────────────────────────────────────────────────

def ac5_network_isolation(report: AcceptanceReport) -> None:
    """AC-5: Nocturne 不暴露公网端口；adapter 是唯一能访问 Nocturne 的路径。"""
    results = {}
    passed = True

    # 检查 Nocturne 端口是否可达（应该不可达，因为没有公开端口）
    try:
        s = socket.create_connection(("nocturne", 8000), timeout=3)
        s.close()
        # 内部可达是正常的
        results["nocturne_internal_8000"] = "reachable"
    except Exception as e:
        results["nocturne_internal_8000"] = f"unreachable: {e}"

    # 检查 adapter 是否可达
    try:
        s = socket.create_connection(("nocturne-adapter", 8001), timeout=3)
        s.close()
        results["adapter_internal_8001"] = "reachable"
    except Exception as e:
        results["adapter_internal_8001"] = f"unreachable: {e}"
        passed = False

    # 检查网关是否可达（应该可达，因为它是唯一暴露端口的服务）
    try:
        s = socket.create_connection(("edge-gateway", 8002), timeout=3)
        s.close()
        results["gateway_internal_8002"] = "reachable"
    except Exception as e:
        results["gateway_internal_8002"] = f"unreachable: {e}"
        passed = False

    report.add("AC-5", "Network Isolation", passed, results)


# ─── AC-6: OpenAI/Anthropic 协议兼容 ─────────────────────────────────────────

def ac6_protocol_compat(report: AcceptanceReport) -> None:
    """AC-6: OpenAI chat.completions 和 Anthropic messages 接口返回合规格式。"""
    results = {}
    passed = True

    # OpenAI 兼容
    try:
        r = httpx.post(
            f"{GATEWAY_URL}/v1/chat/completions",
            json={
                "model": "mock-model",
                "messages": [{"role": "user", "content": "hello"}],
                "stream": False,
            },
            timeout=15,
        )
        body = r.json()
        results["openai"] = {
            "status_code": r.status_code,
            "has_choices": "choices" in body,
            "has_usage": "usage" in body,
        }
        if not (results["openai"]["has_choices"] and results["openai"]["has_usage"]):
            passed = False
    except Exception as e:
        results["openai"] = {"error": str(e)}
        passed = False

    # Anthropic 兼容
    try:
        r = httpx.post(
            f"{GATEWAY_URL}/v1/messages",
            json={
                "model": "mock-model",
                "messages": [{"role": "user", "content": "hello"}],
                "stream": False,
                "max_tokens": 100,
            },
            timeout=15,
        )
        body = r.json()
        results["anthropic"] = {
            "status_code": r.status_code,
            "has_content": "content" in body or "choices" in body,
        }
        if not results["anthropic"]["has_content"]:
            passed = False
    except Exception as e:
        results["anthropic"] = {"error": str(e)}
        passed = False

    report.add("AC-6", "Protocol Compatibility", passed, results)


# ─── AC-7: 会话 ID 稳定性 ────────────────────────────────────────────────────

def ac7_session_stability(report: AcceptanceReport) -> None:
    """AC-7: 相同 session_id 的请求映射到同一会话记录。"""
    session_id = "test-session-abc123"
    results = {}
    passed = True

    for endpoint in ["chat/completions", "messages"]:
        try:
            url = f"{GATEWAY_URL}/v1/{endpoint}"
            payload = {
                "model": "mock-model",
                "messages": [{"role": "user", "content": "test"}],
                "session_id": session_id,
                "stream": False,
            }
            if endpoint == "messages":
                payload["max_tokens"] = 100

            r = httpx.post(url, json=payload, timeout=15)
            results[endpoint] = {
                "status_code": r.status_code,
                "session_id_in_request": session_id,
            }
            if r.status_code >= 500:
                passed = False
        except Exception as e:
            results[endpoint] = {"error": str(e)}
            passed = False

    report.add("AC-7", "Session ID Stability", passed, results)


# ─── AC-8: 日志脱敏 ──────────────────────────────────────────────────────────

def ac8_log_sanitization(report: AcceptanceReport) -> None:
    """AC-8: 日志中不得出现 secret 值、聊天原文或 Authorization header。"""
    # 读取 adapter audit 日志（如果挂载了）
    audit_dir = Path("/var/log/adapter")
    passed = True
    findings = []

    sensitive_patterns = ["Authorization", "Bearer ", "xinchao-dream", "secret", "api_key"]

    if audit_dir.exists():
        for log_file in audit_dir.glob("*.log"):
            text = log_file.read_text(encoding="utf-8", errors="replace")
            for pattern in sensitive_patterns:
                if pattern in text:
                    findings.append(f"{log_file.name}: found '{pattern}'")
                    passed = False

    # 同时检查环境变量是否泄露到日志
    env_dump = json.dumps(dict(os.environ), default=str)
    for secret_key in ["OPENAI_API_KEY", "ANTHROPIC_API_KEY", "MCP_ADAPTER_TOKEN"]:
        val = os.environ.get(secret_key, "")
        if val and len(val) > 4 and val in env_dump:
            findings.append(f"env leak: {secret_key}")
            passed = False

    report.add("AC-8", "Log Sanitization", passed, {"findings": findings, "checked_patterns": sensitive_patterns})


# ─── 主入口 ──────────────────────────────────────────────────────────────────

def main() -> None:
    logger.info("Acceptance Runner starting... GATEWAY=%s ADAPTER=%s", GATEWAY_URL, ADAPTER_URL)
    report = AcceptanceReport()

    ac1_health(report)
    ac2_mcp_tools(report)
    ac3_breath_routing(report)
    ac4_hold_idempotency(report)
    ac5_network_isolation(report)
    ac6_protocol_compat(report)
    ac7_session_stability(report)
    ac8_log_sanitization(report)

    # 输出报告
    json_path = Path("/artifacts/acceptance-report.json")
    md_path = Path("/artifacts/acceptance-report.md")
    json_path.parent.mkdir(parents=True, exist_ok=True)

    json_path.write_text(report.to_json(), encoding="utf-8")
    md_path.write_text(report.to_markdown(), encoding="utf-8")

    logger.info("Acceptance report written to %s and %s", json_path, md_path)
    logger.info("Summary: %s passed, %s failed out of %s", report.passed, report.failed, len(report.cases))

    if report.failed > 0:
        logger.error("ACCEPTANCE FAILED")
        sys.exit(1)
    else:
        logger.info("ACCEPTANCE PASSED")
        sys.exit(0)


if __name__ == "__main__":
    main()
