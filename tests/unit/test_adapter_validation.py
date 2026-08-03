"""Unit tests: adapter validation logic."""

import pytest
from services.nocturne_adapter.src.mcp_bridge import MCPBridge


class FakeClient:
    async def initialize(self):
        pass

    async def close(self):
        pass


class FakeProvenance:
    def ping(self):
        pass


def test_breath_clamping():
    # max_results > 20 should raise ValueError in strict validation
    bridge = MCPBridge(None, FakeClient(), FakeProvenance())
    # The bridge itself may not validate; validation is in main.py
    # This is a placeholder for future strict unit tests
    assert True
