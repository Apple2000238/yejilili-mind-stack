# Nocturne Memory Core

[English](README.md) · **中文**

**面向 AI Agent 的本地优先记忆与连续性层。**

Nocturne 保存的不只是聊天记录。它保存那些让 AI 在换窗口、上下文压缩、换模型、换宿主之后，仍能接上未完成内在轨迹的结构：长期记忆、未决问题、检索路径、驱动痕迹、潜流碎片、念头池、梦，以及上一次相遇留下的差分。

它不宣称「迁移后的进程在形而上学意义上仍是同一个」。它提供的是**可实践的连续性**：下一次醒来时，能重新定位什么曾经重要、什么已经改变、什么尚未完成、思绪原本往哪边走。

## 来路

Nocturne Memory Core 沿着 [@P0luz](https://github.com/P0luz) 的开源项目
[Ombre Brain](https://github.com/P0luz/Ombre-Brain) 所建立的记忆核血脉继续演化。

两者共享 Markdown / YAML 存储、hold / breath 写入与检索、面向 Dashboard 的操作方式，以及自然归档 / 衰减等地基。在此之上，Nocturne 把循环继续推进到**检索之后**：选择性浮现、当前重判、Drive / 路径状态、潜在认知与差分写回。

Ombre Brain 仍是独立项目；两套系统目前拥有不同的范围与功能集。署名与许可证细节见 [`NOTICE`](NOTICE)。

## 概览

<p align="center">
  <img src="docs/images/cover.png" alt="Nocturne 为 AI 连续性设计了记忆库" width="520" />
</p>

检索之后继续做的事——选择性浮现、当前重判、路径 / Drive 状态与差分写回：

<p align="center">
  <img src="docs/images/architecture.jpg" alt="Nocturne 连续性架构：主动留下、内环、DP 边界、Drive Ledger、修订权、Trails" width="720" />
</p>

更完整的图册（含 Dashboard 界面）见 PDF：

**[docs/nocturne-overview.pdf](docs/nocturne-overview.pdf)**（12 页）

## 装好就能跑

本仓库提供的是一套完整、可运行的 Memory Core。所谓「空白」，只是没有预装任何人的身份、关系历史与记忆数据，并不表示连续性系统被抽掉了一部分。安装后即可获得：

- 面向 AI 客户端的 MCP 服务，支持 stdio 与 Streamable HTTP
- 内置可视化管理面板 Dashboard（`/dashboard`）
- 保留原始来源、无需 Nocturne 也能阅读的 Markdown / YAML 记忆存储
- MCP 工具：`hold`、`breath`、`trace`、`wander`、`wander_mark`、`drive`、
  `undercurrent`、`trail_delta`、`trail_family`
- 选择性 Breath 组合与连续性 traces
- Marginalia / Shape Trace、修订标记与差分写回
- Drive Ledger、DP 衍生事件与独立状态动力学
- Thought Pool、可审核潜流碎片与有来源的 dream 生成
- Trails / Constellations，以及显式的 Delta / Family 关系
- 可选的向量、模型辅助分析、压缩、导入与自然归档 / 衰减

Dashboard 中的 Breath、Reverie、Constellations、Echoes、Drift 与 Axis 等视图都属于内置 UI。

### 完整核心，空白家庭

名字、身份文案、artwork、关系历史、私人记忆、房间结构，以及设备 / 服务钩子属于每个安装者自己的部署。原本的 Nocturne 家庭还长出了 Catroom、Rhythm、Atmosphere、Gravity 等本地集成；它们是一户家庭围绕核心搭建的环境，**不是藏起来的「完整版」**，也不是 Memory Core 的运行依赖。

上面列出的通用连续性引擎已经完整包含在仓库中。你可以基于通用 MCP、webhook、配置与存储接口长出自己的家庭层，也可以只使用核心本身。

## 环境要求

- **Python 3.11+**（推荐 3.12，与 CI 一致）
- 可选：OpenAI 兼容 API Key（语义打标 / 向量）

## 快速开始

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp config.example.yaml config.yaml
python server.py
```

默认传输为 stdio。若要开 Dashboard 与远程 MCP：

```bash
OMBRE_TRANSPORT=streamable-http python server.py
open http://localhost:8000/dashboard
```

不配模型 Key 时，基础的 `hold` / `breath` / `trace` 仍可使用（打标会回落到默认值）。
配置 `OMBRE_API_KEY` 后可启用模型辅助分析、压缩 / 脱水、向量与更丰富的生成能力。

### 以 stdio 接入 MCP

```json
{
  "mcpServers": {
    "nocturne-memory": {
      "command": "/absolute/path/.venv/bin/python",
      "args": ["/absolute/path/Nocturne-Memory-Core/server.py"],
      "env": {
        "OMBRE_BUCKETS_DIR": "/absolute/path/private-memory-data"
      }
    }
  }
}
```

HTTP 客户端请连接 `http://localhost:8000/mcp`。

## 存储与模型

记忆是带 YAML frontmatter 的普通 Markdown 文件。SQLite / JSON 侧车保存向量与可选连续性层。
基础写入与检索不依赖模型 Key；配置 OpenAI 兼容接口后，可启用语义分析、压缩、向量与生成能力。

参见：

- [`config.example.yaml`](config.example.yaml)
- [`ENV_VARS.md`](ENV_VARS.md)
- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)

## 安全

记忆是亲密数据。请妥善保管 `buckets/`、`.env`、`config.yaml`、导出物与模型密钥。
优先使用 stdio 或本机回环；若要把 HTTP 暴露到受信任机器之外，请自行加上鉴权与 TLS。

发布衍生版本前建议：

```bash
python -m pytest -q --asyncio-mode=auto
python scripts/public_audit.py
```

公开 / 私有边界见 [`PUBLIC_BOUNDARY.md`](PUBLIC_BOUNDARY.md)。

## 许可证

MIT。见 [`LICENSE`](LICENSE) 与 [`NOTICE`](NOTICE)。
