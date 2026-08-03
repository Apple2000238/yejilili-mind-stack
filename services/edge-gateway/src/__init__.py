"""Edge Gateway — LLM Provider 统一入口

支持协议：
- OpenAI /v1/chat/completions
- Anthropic /v1/messages
- 内部 mock provider（deterministic，用于测试）
"""
