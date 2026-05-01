# openclaw_client.py
# -*- coding: utf-8 -*-
"""
OpenClaw 桥接客户端：将语音识别文字作为 prompt 发送到 OpenClaw 对话。

通过 OpenClaw Gateway 的 OpenAI 兼容 HTTP API (/v1/chat/completions) 发送用户消息，
并可选地获取 AI 回复用于 TTS 播放。

环境变量:
  OPENCLAW_ENABLED: 1 启用 OpenClaw 桥接
  OPENCLAW_GATEWAY_URL: Gateway 地址，默认 http://127.0.0.1:18789
  OPENCLAW_GATEWAY_TOKEN: 可选，Bearer token 认证
"""

import os
import json
import uuid
import asyncio
from typing import Optional, AsyncGenerator, List, Dict, Any

try:
    import aiohttp
    AIOHTTP_AVAILABLE = True
except ImportError:
    AIOHTTP_AVAILABLE = False

# 配置（默认启用，只要 aiohttp 可用且 Gateway 可达即可；设 OPENCLAW_ENABLED=0 可关闭）
_env_enabled = os.getenv("OPENCLAW_ENABLED", "1").strip().lower()
OPENCLAW_ENABLED = _env_enabled not in ("0", "false", "no")
OPENCLAW_GATEWAY_URL = (os.getenv("OPENCLAW_GATEWAY_URL") or "http://127.0.0.1:18789").rstrip("/")
OPENCLAW_SESSION_KEY = os.getenv("OPENCLAW_SESSION_KEY", "agent:main:main").strip()

def _auto_detect_gateway_token() -> str:
    """自动从 ~/.openclaw/openclaw.json 读取 gateway token"""
    token_env = os.getenv("OPENCLAW_GATEWAY_TOKEN", "").strip()
    if token_env:
        return token_env
    try:
        config_path = os.path.expanduser("~/.openclaw/openclaw.json")
        with open(config_path, "r") as f:
            import json as _json
            cfg = _json.load(f)
            t = cfg.get("gateway", {}).get("auth", {}).get("token", "")
            if t:
                print(f"[OpenClaw] 从 ~/.openclaw/openclaw.json 读取到 gateway token", flush=True)
                return t.strip()
    except Exception:
        pass
    return ""

OPENCLAW_GATEWAY_TOKEN = _auto_detect_gateway_token()

if OPENCLAW_ENABLED and AIOHTTP_AVAILABLE:
    print(f"[OpenClaw] 桥接已启用，Gateway: {OPENCLAW_GATEWAY_URL}", flush=True)
elif OPENCLAW_ENABLED and not AIOHTTP_AVAILABLE:
    print("[OpenClaw] 警告：aiohttp 未安装，桥接不可用", flush=True)
else:
    print("[OpenClaw] 桥接已关闭（OPENCLAW_ENABLED=0）", flush=True)


def is_enabled() -> bool:
    """是否启用 OpenClaw 桥接"""
    return OPENCLAW_ENABLED and AIOHTTP_AVAILABLE


async def send_prompt(
    user_text: str,
    *,
    session_key: Optional[str] = None,
    extra_system_prompt: Optional[str] = None,
) -> Optional[str]:
    """
    将用户文字作为 prompt 发送到 OpenClaw，并返回 AI 的完整回复文本。
    非流式，等待完整响应。

    参数:
        user_text: 用户输入（如语音识别结果）
        session_key: 会话键，默认 main
        extra_system_prompt: 可选系统提示词

    返回:
        AI 回复文本，失败返回 None
    """
    if not is_enabled() or not user_text.strip():
        return None

    sk = session_key or OPENCLAW_SESSION_KEY
    messages: List[Dict[str, str]] = []
    if extra_system_prompt:
        messages.append({"role": "system", "content": extra_system_prompt})
    messages.append({"role": "user", "content": user_text.strip()})

    url = f"{OPENCLAW_GATEWAY_URL}/v1/chat/completions"
    headers: Dict[str, str] = {
        "Content-Type": "application/json",
        "X-OpenClaw-Session-Key": sk,
    }
    if OPENCLAW_GATEWAY_TOKEN:
        headers["Authorization"] = f"Bearer {OPENCLAW_GATEWAY_TOKEN}"

    body: Dict[str, Any] = {
        "model": "openclaw",
        "messages": messages,
        "stream": False,
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=body, headers=headers, timeout=aiohttp.ClientTimeout(total=60)) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    print(f"[OpenClaw] HTTP {resp.status}: {text[:200]}", flush=True)
                    return None
                data = await resp.json()
                choices = data.get("choices") or []
                if choices:
                    msg = choices[0].get("message") or {}
                    content = msg.get("content") or ""
                    return content.strip() if content else None
                return None
    except asyncio.TimeoutError:
        print("[OpenClaw] Request timeout", flush=True)
        return None
    except Exception as e:
        print(f"[OpenClaw] Error: {e}", flush=True)
        return None


async def stream_prompt(
    user_text: str,
    *,
    session_key: Optional[str] = None,
    extra_system_prompt: Optional[str] = None,
) -> AsyncGenerator[str, None]:
    """
    将用户文字作为 prompt 发送到 OpenClaw，流式返回 AI 回复的文本增量。

    参数:
        user_text: 用户输入
        session_key: 会话键
        extra_system_prompt: 可选系统提示词

    Yields:
        增量文本片段
    """
    if not is_enabled() or not user_text.strip():
        return

    sk = session_key or OPENCLAW_SESSION_KEY
    messages: List[Dict[str, str]] = []
    if extra_system_prompt:
        messages.append({"role": "system", "content": extra_system_prompt})
    messages.append({"role": "user", "content": user_text.strip()})

    url = f"{OPENCLAW_GATEWAY_URL}/v1/chat/completions"
    headers: Dict[str, str] = {
        "Content-Type": "application/json",
        "X-OpenClaw-Session-Key": sk,
    }
    if OPENCLAW_GATEWAY_TOKEN:
        headers["Authorization"] = f"Bearer {OPENCLAW_GATEWAY_TOKEN}"

    body: Dict[str, Any] = {
        "model": "openclaw",
        "messages": messages,
        "stream": True,
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=body, headers=headers, timeout=aiohttp.ClientTimeout(total=120)) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    print(f"[OpenClaw] HTTP {resp.status}: {text[:200]}", flush=True)
                    return
                async for line in resp.content:
                    line = line.decode("utf-8", errors="replace").strip()
                    if not line or line != "data: [DONE]":
                        if line.startswith("data: "):
                            try:
                                chunk = json.loads(line[6:])
                                for c in (chunk.get("choices") or []):
                                    delta = c.get("delta") or {}
                                    content = delta.get("content")
                                    if content:
                                        yield content
                            except json.JSONDecodeError:
                                pass
    except Exception as e:
        print(f"[OpenClaw] Stream error: {e}", flush=True)


def send_prompt_fire_and_forget(user_text: str, *, session_key: Optional[str] = None) -> None:
    """
    将用户文字发送到 OpenClaw（后台任务，不阻塞调用方）。
    适用于：只需要把语音文字注入 OpenClaw 会话，在 Control UI 中查看对话。
    注意：这是普通函数（非 async），可以在 async 上下文中直接调用。
    """
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(_do_send_prompt(user_text, session_key=session_key))
    except RuntimeError:
        print("[OpenClaw] 无法获取事件循环，跳过发送", flush=True)


async def _do_send_prompt(user_text: str, *, session_key: Optional[str] = None) -> None:
    """实际执行发送的协程，捕获所有异常防止影响主流程"""
    try:
        result = await send_prompt(user_text, session_key=session_key)
        if result:
            print(f"[OpenClaw] AI 回复: {result[:80]}{'…' if len(result) > 80 else ''}", flush=True)
        else:
            print("[OpenClaw] 发送完成（无回复或回复为空）", flush=True)
    except Exception as e:
        print(f"[OpenClaw] 后台发送失败: {e}", flush=True)


def get_session_key() -> str:
    """返回当前配置的 session key"""
    return OPENCLAW_SESSION_KEY
