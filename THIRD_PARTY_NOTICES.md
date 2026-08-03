# Third-Party Notices

本仓库包含以下第三方开源组件。所有原始许可证和版权声明均已保留。

---

## Nocturne Memory Core

- **来源**: https://github.com/Pyruslili/Nocturne-Memory-Core
- **Commit**: `8fecd3bbce9025bf05e2c6ef2311dfe4341ef38b`
- **许可证**: MIT
- **许可证文件**: [upstream/nocturne-memory-core/LICENSE](upstream/nocturne-memory-core/LICENSE)
- **NOTICE 文件**: [upstream/nocturne-memory-core/NOTICE](upstream/nocturne-memory-core/NOTICE)

Nocturne Memory Core 是 Ombre Brain 的进化版。其 NOTICE 文件要求保留对 Ombre Brain 的来源说明。本仓库以只读 vendor 形式引用 Nocturne 固定快照，不做源码修改。

---

## XinChao Dynamic Mind

- **来源**: https://github.com/Apple2000238/xinchao-dynamic-mind
- **Commit**: `9c36803629a98b95a4ec73c58809809800e10e6b`
- **许可证**: MIT
- **许可证文件**: [upstream/xinchao-dynamic-mind/LICENSE](upstream/xinchao-dynamic-mind/LICENSE)

本仓库以只读 vendor 形式引用 XinChao 固定快照，不做源码修改。

---

## 适配层声明

本仓库新增的 `nocturne-adapter` 服务是独立编写的兼容适配层，用于解决 XinChao 与 Nocturne 之间的协议兼容缺口（`breath` 参数路由、`hold(auto/source)` 语义等），不修改上游源码。
