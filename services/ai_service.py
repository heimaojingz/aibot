import logging
from typing import Optional

from config import config

logger = logging.getLogger(__name__)

# ── AI Model Registry ───────────────────────────────────────────────────────
# 14 models with their API endpoints and capabilities

MODEL_REGISTRY = {
    "gpt-4o": {
        "name": "GPT-4o",
        "provider": "openai",
        "api_base": "https://api.openai.com/v1",
        "default_key_env": "OPENAI_API_KEY",
        "max_tokens": 4096,
        "supports_vision": True,
    },
    "gpt-4o-mini": {
        "name": "GPT-4o Mini",
        "provider": "openai",
        "api_base": "https://api.openai.com/v1",
        "default_key_env": "OPENAI_API_KEY",
        "max_tokens": 4096,
        "supports_vision": True,
    },
    "gpt-4-turbo": {
        "name": "GPT-4 Turbo",
        "provider": "openai",
        "api_base": "https://api.openai.com/v1",
        "default_key_env": "OPENAI_API_KEY",
        "max_tokens": 4096,
        "supports_vision": True,
    },
    "gpt-4": {
        "name": "GPT-4",
        "provider": "openai",
        "api_base": "https://api.openai.com/v1",
        "default_key_env": "OPENAI_API_KEY",
        "max_tokens": 8192,
        "supports_vision": False,
    },
    "gpt-3.5-turbo": {
        "name": "GPT-3.5 Turbo",
        "provider": "openai",
        "api_base": "https://api.openai.com/v1",
        "default_key_env": "OPENAI_API_KEY",
        "max_tokens": 4096,
        "supports_vision": False,
    },
    "claude-3.5-sonnet": {
        "name": "Claude 3.5 Sonnet",
        "provider": "anthropic",
        "api_base": "https://api.anthropic.com/v1",
        "default_key_env": "ANTHROPIC_API_KEY",
        "max_tokens": 4096,
        "supports_vision": True,
    },
    "claude-3-opus": {
        "name": "Claude 3 Opus",
        "provider": "anthropic",
        "api_base": "https://api.anthropic.com/v1",
        "default_key_env": "ANTHROPIC_API_KEY",
        "max_tokens": 4096,
        "supports_vision": True,
    },
    "claude-3-haiku": {
        "name": "Claude 3 Haiku",
        "provider": "anthropic",
        "api_base": "https://api.anthropic.com/v1",
        "default_key_env": "ANTHROPIC_API_KEY",
        "max_tokens": 4096,
        "supports_vision": True,
    },
    "gemini-1.5-pro": {
        "name": "Gemini 1.5 Pro",
        "provider": "google",
        "api_base": "https://generativelanguage.googleapis.com/v1beta",
        "default_key_env": "GEMINI_API_KEY",
        "max_tokens": 8192,
        "supports_vision": True,
    },
    "gemini-1.5-flash": {
        "name": "Gemini 1.5 Flash",
        "provider": "google",
        "api_base": "https://generativelanguage.googleapis.com/v1beta",
        "default_key_env": "GEMINI_API_KEY",
        "max_tokens": 8192,
        "supports_vision": True,
    },
    "deepseek-v3": {
        "name": "DeepSeek V3",
        "provider": "deepseek",
        "api_base": "https://api.deepseek.com/v1",
        "default_key_env": "DEEPSEEK_API_KEY",
        "max_tokens": 4096,
        "supports_vision": False,
    },
    "deepseek-r1": {
        "name": "DeepSeek R1",
        "provider": "deepseek",
        "api_base": "https://api.deepseek.com/v1",
        "default_key_env": "DEEPSEEK_API_KEY",
        "max_tokens": 4096,
        "supports_vision": False,
    },
    "qwen-max": {
        "name": "Qwen Max",
        "provider": "alibaba",
        "api_base": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "default_key_env": "DASHSCOPE_API_KEY",
        "max_tokens": 4096,
        "supports_vision": False,
    },
    "qwen-plus": {
        "name": "Qwen Plus",
        "provider": "alibaba",
        "api_base": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "default_key_env": "DASHSCOPE_API_KEY",
        "max_tokens": 4096,
        "supports_vision": False,
    },
}

# ── In-memory conversation history ──────────────────────────────────────────
# { group_id: [ { "role": "user"/"assistant", "content": "..." }, ... ] }
_conversation_history: dict[int, list[dict]] = {}


def get_model_info(model_id: str) -> Optional[dict]:
    """Get model metadata by ID."""
    return MODEL_REGISTRY.get(model_id)


def list_available_models() -> list[dict]:
    """List all available models with metadata."""
    return [{"id": k, **v} for k, v in MODEL_REGISTRY.items()]


def get_history(group_id: int, max_history: int = 10) -> list[dict]:
    """Get recent conversation history for a group."""
    history = _conversation_history.get(group_id, [])
    return history[-max_history * 2:]  # Each turn has user+assistant


def add_to_history(group_id: int, role: str, content: str, max_history: int = 10):
    """Add a message to conversation history."""
    if group_id not in _conversation_history:
        _conversation_history[group_id] = []
    _conversation_history[group_id].append({"role": role, "content": content})
    # Trim to max_history turns
    limit = max_history * 2
    if len(_conversation_history[group_id]) > limit:
        _conversation_history[group_id] = _conversation_history[group_id][-limit:]


def clear_history(group_id: int):
    """Clear conversation history for a group."""
    _conversation_history.pop(group_id, None)


async def chat_completion(
    model_id: str,
    messages: list[dict],
    api_key: str = "",
    api_base: str = "",
    system_prompt: str = "",
    temperature: float = 0.7,
    max_tokens: int = 2048,
) -> str:
    """Send a chat completion request to the configured AI model.

    Supports OpenAI-compatible API format (works for GPT, DeepSeek, Qwen).
    For Claude and Gemini, calls are adapted to their native formats.
    """
    model_info = get_model_info(model_id)
    if not model_info:
        return f"❌ 未知模型：{model_id}"

    provider = model_info["provider"]
    effective_key = api_key or config.AI_DEFAULT_API_KEY or ""
    effective_base = api_base or model_info["api_base"]

    if not effective_key:
        return "❌ AI 服务未配置 API Key。请联系管理员设置。"

    try:
        if provider in ("openai", "deepseek", "alibaba"):
            return await _openai_compatible(
                effective_base, effective_key, model_id, messages,
                system_prompt, temperature, max_tokens
            )
        elif provider == "anthropic":
            return await _anthropic_chat(
                effective_key, model_id, messages,
                system_prompt, temperature, max_tokens
            )
        elif provider == "google":
            return await _google_chat(
                effective_key, model_id, messages,
                system_prompt, temperature, max_tokens
            )
        else:
            return f"❌ 不支持的提供商：{provider}"
    except Exception as e:
        logger.error(f"AI chat error: {e}", exc_info=True)
        return f"❌ AI 服务暂时不可用：{str(e)[:200]}"


async def _openai_compatible(
    base_url: str, api_key: str, model: str,
    messages: list[dict], system_prompt: str,
    temperature: float, max_tokens: int,
) -> str:
    """Call any OpenAI-compatible API."""
    import aiohttp

    url = f"{base_url.rstrip('/')}/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    full_messages = []
    if system_prompt:
        full_messages.append({"role": "system", "content": system_prompt})
    full_messages.extend(messages)

    payload = {
        "model": model,
        "messages": full_messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }

    async with aiohttp.ClientSession() as session:
        async with session.post(url, json=payload, headers=headers, timeout=30) as resp:
            if resp.status != 200:
                error_text = await resp.text()
                logger.error(f"OpenAI API error {resp.status}: {error_text}")
                return f"❌ API 错误 ({resp.status})"
            data = await resp.json()
            return data["choices"][0]["message"]["content"]


async def _anthropic_chat(
    api_key: str, model: str, messages: list[dict],
    system_prompt: str, temperature: float, max_tokens: int,
) -> str:
    """Call Anthropic Messages API."""
    import aiohttp

    url = "https://api.anthropic.com/v1/messages"
    headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "Content-Type": "application/json",
    }
    # Convert to Anthropic format
    anthropic_messages = []
    for m in messages:
        anthropic_messages.append({"role": m["role"], "content": m["content"]})

    payload = {
        "model": model,
        "messages": anthropic_messages,
        "system": system_prompt,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }

    async with aiohttp.ClientSession() as session:
        async with session.post(url, json=payload, headers=headers, timeout=30) as resp:
            if resp.status != 200:
                error_text = await resp.text()
                logger.error(f"Anthropic API error {resp.status}: {error_text}")
                return f"❌ API 错误 ({resp.status})"
            data = await resp.json()
            return data["content"][0]["text"]


async def _google_chat(
    api_key: str, model: str, messages: list[dict],
    system_prompt: str, temperature: float, max_tokens: int,
) -> str:
    """Call Gemini API."""
    import aiohttp

    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        f"?key={api_key}"
    )
    contents = []
    for m in messages:
        contents.append({
            "role": "user" if m["role"] == "user" else "model",
            "parts": [{"text": m["content"]}],
        })

    payload = {
        "contents": contents,
        "generationConfig": {
            "temperature": temperature,
            "maxOutputTokens": max_tokens,
        },
    }
    if system_prompt:
        payload["systemInstruction"] = {"parts": [{"text": system_prompt}]}

    async with aiohttp.ClientSession() as session:
        async with session.post(url, json=payload, timeout=30) as resp:
            if resp.status != 200:
                error_text = await resp.text()
                logger.error(f"Gemini API error {resp.status}: {error_text}")
                return f"❌ API 错误 ({resp.status})"
            data = await resp.json()
            return data["candidates"][0]["content"]["parts"][0]["text"]


# ── Spam detection via AI ───────────────────────────────────────────────────

async def ai_spam_check(text: str, api_key: str = "") -> bool:
    """Use AI to detect if a message is spam/advertisement."""
    prompt = (
        "请判断以下消息是否为广告或垃圾消息。仅回复 JSON："
        '{"is_spam": true/false, "confidence": 0.0-1.0, "reason": "简短理由"}'
        f"\n\n消息内容：\n{text[:1000]}"
    )
    try:
        result = await chat_completion(
            model_id="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            api_key=api_key or config.AI_DEFAULT_API_KEY,
            temperature=0.1,
            max_tokens=256,
        )
        import json
        data = json.loads(result.strip())
        return data.get("is_spam", False) and data.get("confidence", 0) > 0.7
    except Exception:
        return False
