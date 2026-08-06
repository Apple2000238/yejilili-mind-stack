# R6-01 技术限制记录：uvloop 平台依赖

> 记录日期：2026-08-06  
> 记录人：Kimi  
> 对应任务：R6-01 可复现依赖、构建与 CI 闭包  

---

## 问题描述

`services/nocturne-adapter/requirements.txt` 使用 `uvicorn[standard]>=0.30.0`，该 extras 组包含 `uvloop>=0.15.1` 作为传递依赖。

运行 `pip-compile --generate-hashes` 后，生成的 `requirements.lock` 中**不包含 uvloop**。

## 根因分析

uvicorn 的 METADATA 中 uvloop 的依赖声明为：

```
Requires-Dist: uvloop>=0.15.1; (sys_platform != 'win32' and (sys_platform != 'cygwin' and platform_python_implementation != 'PyPy')) and extra == 'standard'
```

关键条件：`sys_platform != 'win32'`

当前 Kimi Work 执行环境为 **Windows**，pip-compile 在解析依赖时：
1. 识别到 uvloop 的平台限制为 `sys_platform != 'win32'`
2. 由于当前平台是 Windows，pip-compile 判定 uvloop **不适用**
3. 因此 uvloop 未被纳入 lock 文件

## 验证

- `pip install uvloop` 在 Windows 上直接报错：`RuntimeError: uvloop does not support Windows at the moment`
- Docker 不可用（`docker not available`）
- WSL 已安装但未配置 Linux 发行版（`wsl --list` 输出帮助信息，无可用 distro）

## 影响

在 Linux 构建环境（Docker、VPS）中执行 `pip install -r requirements.lock --require-hashes` 时：
- uvicorn[standard] 会被安装
- 但 uvloop 不在 lock 文件中
- `--require-hashes` 模式下，pip 可能拒绝安装未锁定的 uvloop，导致构建失败

## 替代方案与原方案差异

| 方案 | 做法 | 差异 | 副作用 |
|---|---|---|---|
| **原方案** | 在 Windows 本地运行 `pip-compile --generate-hashes` 生成完整 lock | 不可行，uvloop 无法解析 | — |
| **替代 A** | 在 Linux/Docker 环境中运行 pip-compile | 需要 Linux 环境，当前不可用 | 无，推荐方案 |
| **替代 B** | 在 requirements.txt 中显式添加 `uvloop>=0.15.1 ; sys_platform != "win32"` | 让 pip-compile 显式记录平台条件 | lock 文件中仍无 uvloop（Windows 上），Linux 构建时 pip 会从 PyPI 安装（但 `--require-hashes` 可能拒绝） |
| **替代 C** | 放弃 `uvicorn[standard]`，改用 `uvicorn` 基础版 | 失去 watchfiles、httptools 等标准 extras | 功能降级，需验证是否影响 nocturne-adapter |
| **替代 D** | 在 CI/GitHub Actions 中运行 pip-compile（Linux runner） | 利用 GitHub Actions 的 Ubuntu runner 生成 lock | lock 文件在 CI 中生成而非本地，仍可复现 |

## 建议

**推荐替代 A + D 组合**：
1. 短期：使用 GitHub Actions Ubuntu runner 运行 `pip-compile --generate-hashes`，确保 lock 文件包含 uvloop
2. 长期：建立 Linux 开发环境（Docker/WSL）用于依赖锁定

**不推荐替代 C**：`uvicorn[standard]` 的 watchfiles 和 httptools 对 nocturne-adapter 的开发体验有实际价值，不应轻易降级。

---

## 等待夜霁判断

请夜霁决定：
1. 是否接受在 GitHub Actions（Linux runner）中生成包含 uvloop 的 lock 文件？
2. 或者是否有其他方案？
3. 在此之前，R6-01 的 nocturne-adapter 依赖闭包部分保持 BLOCKED。

---

## 其他服务 lock 文件状态

以下服务的 lock 文件**无 uvloop 等平台限制问题**，可正常在 Windows 上重新生成：
- `services/continuity-guard/requirements.lock` ✅
- `services/edge-gateway/requirements.lock` ✅
- `services/migration-cli/requirements.lock` ✅
- `services/acceptance-runner/requirements.lock` ✅
