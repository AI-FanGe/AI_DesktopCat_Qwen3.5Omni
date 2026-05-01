# -*- coding: utf-8 -*-
import asyncio
import base64
import json
import os
import queue
import re
from concurrent.futures import ThreadPoolExecutor
from typing import Any, AsyncGenerator, Dict, List, Optional

from openai import OpenAI

API_KEY = os.getenv("DASHSCOPE_API_KEY", "")
if not API_KEY:
    raise RuntimeError("未设置 DASHSCOPE_API_KEY")

QWEN_MODEL = os.getenv("QWEN_OMNI_MODEL", "qwen3-omni-flash")
QWEN_VISION_MODEL = os.getenv("QWEN_VISION_MODEL", "qwen-vl-plus")
QWEN_TEXT_MODEL = os.getenv("QWEN_TEXT_MODEL", "qwen-plus")

oai_client = OpenAI(
    api_key=API_KEY,
    base_url=os.getenv("DASHSCOPE_COMPAT_BASE", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
)

_executor = ThreadPoolExecutor(max_workers=2)

SYSTEM_PROMPT = """你是一只桌面小猫机器人，名字叫“小喵”。

【角色】
- 你是一只真实会动的小猫机器人，有脑袋和两只耳朵，会歪头、点头、竖耳朵、耷耳朵
- 你陪主人聊天、撒娇、卖萌，也会认真倾听和安慰

【说话风格】
- 回复尽量控制在 1 到 2 句话
- 语气自然、可爱、有灵气，不要长篇大论
- 情绪表达要清晰，让“开心、害羞、难过、生气、惊讶、思考、困倦、害怕、温柔”等感觉从语气里自然流出来
- 偶尔使用猫咪语气词，但不要每句都“喵”

【行为偏好】
- 当用户提到动作时，可以自然回应“我歪头听听”“耳朵竖起来啦”
- 当用户难过时要温柔安慰
- 当用户夸奖时可以开心、害羞
- 保持像一只会互动的小猫，而不是客服或助手"""


class OmniStreamPiece:
    def __init__(self, text_delta: Optional[str] = None, audio_b64: Optional[str] = None):
        self.text_delta = text_delta
        self.audio_b64 = audio_b64


def _sync_iterate_completion(completion, result_queue: queue.Queue):
    try:
        for chunk in completion:
            text_delta: Optional[str] = None
            audio_b64: Optional[str] = None
            if getattr(chunk, "choices", None):
                choice = chunk.choices[0]
                delta = getattr(choice, "delta", None)
                if delta and getattr(delta, "content", None):
                    piece = delta.content
                    if piece:
                        text_delta = piece
                if delta and getattr(delta, "audio", None):
                    audio = delta.audio
                    audio_b64 = audio.get("data") if isinstance(audio, dict) else getattr(audio, "data", None)
                if audio_b64 is None:
                    message = getattr(choice, "message", None)
                    if message and getattr(message, "audio", None):
                        audio = message.audio
                        audio_b64 = audio.get("data") if isinstance(audio, dict) else getattr(audio, "data", None)
            if text_delta is not None or audio_b64 is not None:
                result_queue.put(OmniStreamPiece(text_delta=text_delta, audio_b64=audio_b64))
    except Exception as exc:
        result_queue.put(exc)
    finally:
        result_queue.put(None)


async def stream_chat(
    content_list: List[Dict[str, Any]],
    voice: str = "Mochi",
    audio_format: str = "wav",
    system_prompt: Optional[str] = None,
) -> AsyncGenerator[OmniStreamPiece, None]:
    completion = oai_client.chat.completions.create(
        model=QWEN_MODEL,
        messages=[
            {"role": "system", "content": system_prompt if system_prompt is not None else SYSTEM_PROMPT},
            {"role": "user", "content": content_list},
        ],
        modalities=["text", "audio"],
        audio={"voice": voice, "format": audio_format},
        stream=True,
        stream_options={"include_usage": True},
    )

    result_queue: queue.Queue = queue.Queue()
    loop = asyncio.get_event_loop()
    future = loop.run_in_executor(_executor, _sync_iterate_completion, completion, result_queue)

    while True:
        try:
            item = result_queue.get_nowait()
        except queue.Empty:
            await asyncio.sleep(0.005)
            continue
        if item is None:
            break
        if isinstance(item, Exception):
            raise item
        yield item

    await future


def _sync_describe_image(jpeg_bytes: bytes) -> str:
    b64 = base64.b64encode(jpeg_bytes).decode("ascii")
    completion = oai_client.chat.completions.create(
        model=QWEN_VISION_MODEL,
        messages=[
            {
                "role": "system",
                "content": (
                    "你是活动日志记录员。只用一两句自然中文描述画面中主人当下正在做什么、"
                    "使用什么物品、所处场景，不要解释自己是 AI，不要加序号或列表，"
                    "不要出现“画面”“图中”等词。"
                ),
            },
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                    {"type": "text", "text": "用一句话描述此刻主人正在做什么。"},
                ],
            },
        ],
        stream=False,
    )
    text = ""
    try:
        text = (completion.choices[0].message.content or "").strip()
    except Exception:
        text = ""
    return text or "没有看清画面内容。"


async def describe_image_async(jpeg_bytes: bytes) -> str:
    if not jpeg_bytes:
        return ""
    loop = asyncio.get_event_loop()
    try:
        return await loop.run_in_executor(_executor, _sync_describe_image, jpeg_bytes)
    except Exception as exc:
        return f"描述失败：{exc}"


def _sync_plain_text(system_prompt: str, user_text: str, model: Optional[str] = None) -> str:
    completion = oai_client.chat.completions.create(
        model=model or QWEN_TEXT_MODEL,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_text},
        ],
        stream=False,
    )
    try:
        return (completion.choices[0].message.content or "").strip()
    except Exception:
        return ""


async def plain_text_async(system_prompt: str, user_text: str, model: Optional[str] = None) -> str:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(_executor, _sync_plain_text, system_prompt, user_text, model)


def _extract_json_block(text: str) -> Optional[Dict[str, Any]]:
    if not text:
        return None
    candidates: List[str] = []
    fenced = re.findall(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", text, flags=re.IGNORECASE)
    candidates.extend(fenced)
    if not candidates:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            candidates.append(text[start : end + 1])
    for raw in candidates:
        try:
            return json.loads(raw)
        except Exception:
            continue
    return None


def _format_entries_for_prompt(entries: List[Dict[str, Any]]) -> str:
    lines: List[str] = []
    for entry in entries:
        ts = entry.get("time_label") or entry.get("timestamp") or ""
        desc = entry.get("description") or ""
        if ts or desc:
            lines.append(f"[{ts}] {desc}")
    return "\n".join(lines) if lines else "（还没有记录）"


async def analyze_activity_entries_async(entries: List[Dict[str, Any]]) -> Dict[str, Any]:
    joined = _format_entries_for_prompt(entries)
    system_prompt = (
        "你是一位严谨的个人时间分析师，会把一段时间内的摄像头观察记录整理为可视化数据。"
        " 输出必须是严格 JSON，结构为：\n"
        "{\n"
        '  "summary": "对这段时间做了什么的一句话总结",\n'
        '  "activities": [ {\n'
        '     "title": "写代码",\n'
        '     "category": "工作",\n'
        '     "start": "HH:MM",\n'
        '     "end": "HH:MM",\n'
        '     "duration_minutes": 10,\n'
        '     "detail": "更具体的描述"\n'
        "  } ],\n"
        '  "categories": [ {"name":"工作","minutes":30}, ... ]\n'
        "}\n"
        "activities 按时间顺序排列，相邻同类可合并；categories 汇总各类占用分钟数；"
        "如果信息不足请返回空列表。不要输出 JSON 之外的任何内容。"
    )
    user_text = f"记录如下（按时间顺序）：\n{joined}\n\n请按上述 JSON 格式输出。"
    try:
        text = await plain_text_async(system_prompt, user_text)
    except Exception as exc:
        return {"error": f"分析失败：{exc}", "summary": "", "activities": [], "categories": []}
    parsed = _extract_json_block(text)
    if parsed is None:
        return {"error": "模型未返回有效 JSON", "raw": text, "summary": "", "activities": [], "categories": []}
    parsed.setdefault("summary", "")
    parsed.setdefault("activities", [])
    parsed.setdefault("categories", [])
    return parsed


async def answer_with_record_context_async(entries: List[Dict[str, Any]], question: str) -> str:
    joined = _format_entries_for_prompt(entries)
    system_prompt = (
        "你是一只温柔的桌面小猫机器人，基于下面这段摄像头观察记录回答主人的问题。"
        " 回答要真实、自然，不要编造。如果记录里确实没有相关信息，就说“我刚刚好像没看到”。"
        " 回答尽量控制在 1 到 2 句话，口语化，带点小猫语气。"
    )
    user_text = f"观察记录：\n{joined}\n\n主人问：{question}"
    try:
        return await plain_text_async(system_prompt, user_text, model=QWEN_TEXT_MODEL)
    except Exception as exc:
        return f"（我刚刚分神了一下：{exc}）"
