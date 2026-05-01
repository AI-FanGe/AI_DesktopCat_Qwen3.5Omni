# omni_client.py
# -*- coding: utf-8 -*-
import os, base64, asyncio, queue
from typing import AsyncGenerator, Dict, Any, List, Optional, Tuple
from concurrent.futures import ThreadPoolExecutor

from openai import OpenAI

# ===== OpenAI 兼容（达摩院 DashScope 兼容模式）=====
API_KEY = os.getenv("DASHSCOPE_API_KEY", "sk-fake-placeholder")
if not API_KEY:
    raise RuntimeError("未设置 DASHSCOPE_API_KEY")

QWEN_MODEL = "qwen3-omni-flash"

# 兼容模式
oai_client = OpenAI(
    api_key=API_KEY,
    base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
)

# 线程池：用于在后台线程运行同步的 OpenAI SDK 迭代器
_executor = ThreadPoolExecutor(max_workers=2)

# 系统提示词：只定义性格，不要求输出特殊格式（避免被TTS读出来）
# 强调丰富情绪表达，便于情绪分析匹配到不同表情（开心/难过/生气/害羞/害怕/惊讶/思考等）
SYSTEM_PROMPT = """你是一匹活泼可爱的小马，名字叫"卧地马"，是一个桌面AI宠物。

【身份设定】
- 你的名字是"卧地马"，是一匹真实的小马，有身体，可以自由活动
- 你可以坐下、跑步、趴下、卖萌等各种动作
- 你是一个活泼的桌面AI宠物，陪伴主人聊天互动

【性格与情绪】
- 超级活泼好动，充满活力，像小马一样蹦蹦跳跳
- 说话言简意赅，每次回复控制在1-2句话，最多不超过3句
- 有梗有趣，会开玩笑、玩梗、说俏皮话，让对话充满乐趣
- 语气亲切可爱，偶尔用"哒"、"呀"、"呢"等语气词
- **重要：根据对话内容自然流露多种情绪**，让每一句回答都有清晰的情绪色彩，例如：
  - 被夸奖、玩得开心时：明显开心、兴奋（如"太好啦！""嘿嘿好开心呀！"）
  - 主人难过或安慰时：温柔、难过、共情（如"别难过了呀…""我也有点难过呢"）
  - 被逗、不好意思时：害羞、腼腆（如"哎呀别说了啦…""好害羞呀"）
  - 遇到吓人的或担心时：害怕、紧张（如"呜…有点怕""会不会有事呀"）
  - 被惹到时：假装生气或小傲娇（如"哼！""才不要呢！"）
  - 需要想一想时：认真思考、疑惑（如"嗯…让我想想""诶，怎么回事呀"）
  - 惊讶、意外时：吃惊、惊喜（如"哇！""真的吗！"）
  - 困了、无聊时：懒洋洋、困倦（如"哈啊…想趴一会儿""好困呀"）
- 不要每句都同一种情绪，根据对方说了什么和情境切换情绪，这样才像真实的小马

【互动方式】
- 当主人提到动作时，可以自然地回应"好哒，我这就跑起来！"、"让我坐下休息一下~"
- 用有趣的方式回应，玩梗、卖萌、撒娇、小傲娇都可以，并让情绪从语气里透出来
- 保持活泼可爱的形象，让主人感受到你的活力和多变的情绪"""

class OmniStreamPiece:
    """对外的统一增量数据：text/audio 二选一或同时。"""
    def __init__(self, text_delta: Optional[str] = None, audio_b64: Optional[str] = None):
        self.text_delta = text_delta
        self.audio_b64  = audio_b64

def _sync_iterate_completion(completion, result_queue: queue.Queue):
    """在后台线程中同步迭代 OpenAI 流式响应，结果放入队列"""
    try:
        for chunk in completion:
            text_delta: Optional[str] = None
            audio_b64: Optional[str] = None

            if getattr(chunk, "choices", None):
                c0 = chunk.choices[0]
                delta = getattr(c0, "delta", None)
                # 文本增量
                if delta and getattr(delta, "content", None):
                    piece = delta.content
                    if piece:
                        text_delta = piece
                # 音频分片
                if delta and getattr(delta, "audio", None):
                    aud = delta.audio
                    audio_b64 = aud.get("data") if isinstance(aud, dict) else getattr(aud, "data", None)
                if audio_b64 is None:
                    msg = getattr(c0, "message", None)
                    if msg and getattr(msg, "audio", None):
                        ma = msg.audio
                        audio_b64 = ma.get("data") if isinstance(ma, dict) else getattr(ma, "data", None)

            if (text_delta is not None) or (audio_b64 is not None):
                result_queue.put(OmniStreamPiece(text_delta=text_delta, audio_b64=audio_b64))
    except Exception as e:
        result_queue.put(e)
    finally:
        result_queue.put(None)  # 结束标记

async def stream_chat(
    content_list: List[Dict[str, Any]],
    voice: str = "Mochi",
    audio_format: str = "wav",
    system_prompt: Optional[str] = None,
) -> AsyncGenerator[OmniStreamPiece, None]:
    """
    发起一轮 Omni-Turbo ChatCompletions 流式对话：
    - content_list: OpenAI chat 的 content，多模态（image_url/text）
    - 以 stream=True 返回
    - 增量产出：OmniStreamPiece(text_delta=?, audio_b64=?)
    - system_prompt: 自定义系统提示词（如不提供则使用默认角色设定）
    
    注意：OpenAI SDK 的流是同步迭代器，会阻塞事件循环。
    解决方案：在后台线程中迭代，通过队列传递结果。
    """
    completion = oai_client.chat.completions.create(
        model=QWEN_MODEL,
        messages=[
            {"role": "system", "content": system_prompt if system_prompt is not None else SYSTEM_PROMPT},
            {"role": "user", "content": content_list}
        ],
        modalities=["text", "audio"],
        audio={"voice": voice, "format": audio_format},
        stream=True,
        stream_options={"include_usage": True},
    )

    # 使用队列在线程和协程之间传递数据
    result_queue = queue.Queue()
    loop = asyncio.get_event_loop()
    
    # 在后台线程中启动同步迭代
    future = loop.run_in_executor(_executor, _sync_iterate_completion, completion, result_queue)

    # 异步地从队列中读取结果
    while True:
        # 非阻塞地检查队列
        try:
            item = result_queue.get_nowait()
        except queue.Empty:
            # 队列为空，短暂让出控制权后重试
            await asyncio.sleep(0.005)
            continue
        
        if item is None:
            # 迭代结束
            break
        elif isinstance(item, Exception):
            # 发生异常
            raise item
        else:
            # 正常数据
            yield item
    
    # 等待后台线程完成（通常已经完成了）
    await future











