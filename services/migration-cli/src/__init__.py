"""Migration CLI — 数据迁移工具骨架

待实现：
- migrate-one: 单条记录迁移
- migrate-batch: 批量迁移
- rollback: 回滚指定批次
- verify: 校验迁移结果
"""

from __future__ import annotations

import logging
import os

import psycopg

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("migration-cli")

DSN = (
    f"postgresql://{os.environ['POSTGRES_USER']}:{open(os.environ['POSTGRES_PASSWORD_FILE']).read().strip()}"
    f"@{os.environ['POSTGRES_HOST']}:{os.environ['POSTGRES_PORT']}/{os.environ['POSTGRES_DB']}"
)


def main() -> None:
    logger.info("Migration CLI ready. DSN host=%s", os.environ.get("POSTGRES_HOST"))
    # TODO: implement CLI commands
    print("Available commands: migrate-one, migrate-batch, rollback, verify")


if __name__ == "__main__":
    main()
