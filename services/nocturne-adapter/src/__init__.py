"""Nocturne Adapter — XinChao ↔ Nocturne 兼容适配层

解决核心兼容缺口：
- breath(query, max_results, max_tokens) → 有 query 调用 trace()，无 query 调用零参 breath()
- hold(auto, source) → 转换为 tags + provenance 账本记录
"""
