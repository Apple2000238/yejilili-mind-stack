"""Acceptance Runner — 验收测试骨架

待实现：
- AC-1 ~ AC-8 自动化验收用例
"""

from __future__ import annotations

import logging
import os

import httpx

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("acceptance-runner")

GATEWAY_URL = os.environ.get("GATEWAY_URL", "http://edge-gateway:8002")
ADAPTER_URL = os.environ.get("ADAPTER_URL", "http://nocturne-adapter:8001")


def test_health() -> bool:
    try:
        r = httpx.get(f"{GATEWAY_URL}/health", timeout=5)
        logger.info("gateway health: %s", r.json())
        return r.status_code == 200
    except Exception as e:
        logger.error("gateway health failed: %s", e)
        return False


def main() -> None:
    logger.info("Acceptance Runner starting...")
    results = {"health": test_health()}
    logger.info("Results: %s", results)
    # TODO: implement full AC-1 ~ AC-8


if __name__ == "__main__":
    main()
