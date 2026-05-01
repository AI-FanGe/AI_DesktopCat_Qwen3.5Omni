# emotion_parser.py
# -*- coding: utf-8 -*-
"""
情绪解析模块：使用 Qwen Turbo 从 AI 回复文本中分析情绪
并映射到 LED 颜色配置
"""
import os
from typing import Tuple, Dict
from openai import OpenAI

# 复用 omni_client 的 API Key
API_KEY = os.getenv("DASHSCOPE_API_KEY", "sk-fake-placeholder")

# ====================================================================
# 情绪到 LED 颜色映射
# 格式: (R, G, B, 亮度)
# ====================================================================
EMOTION_LED_MAP: Dict[str, Tuple[int, int, int, int]] = {
    "happy":     (0, 255, 0, 180),      # 开心 - 绿色
    "sad":       (0, 0, 255, 120),      # 难过 - 蓝色
    "angry":     (255, 0, 0, 200),      # 生气 - 红色
    "surprised": (255, 255, 0, 200),    # 惊讶 - 黄色
    "thinking":  (128, 0, 255, 150),    # 思考 - 紫色
    "sleepy":    (100, 100, 100, 80),   # 困倦 - 暗灰色
    "excited":   (255, 128, 0, 220),    # 兴奋 - 橙色
    "confused":  (255, 0, 255, 150),    # 困惑 - 品红
    "love":      (255, 105, 180, 200),  # 喜爱 - 粉色
    "neutral":   (255, 255, 255, 120),  # 中性 - 白色
    "fear":      (128, 0, 128, 150),    # 害怕 - 深紫色
    "shy":       (255, 182, 193, 150),  # 害羞 - 浅粉色
}

# 默认情绪（无法识别时使用）
DEFAULT_EMOTION = "neutral"


def _make_client() -> OpenAI:
    """创建 OpenAI 兼容客户端"""
    base_url = os.getenv("DASHSCOPE_COMPAT_BASE", "https://dashscope.aliyuncs.com/compatible-mode/v1")
    return OpenAI(api_key=API_KEY, base_url=base_url)


# Qwen Turbo 情绪提取提示词
EMOTION_EXTRACT_PROMPT = """你是一个情绪分析助手。分析给定文本的情绪，返回一个情绪标签。

可用的情绪标签（只能返回以下之一）：
- happy: 开心、高兴、满足、愉快
- sad: 难过、伤心、失落、沮丧
- angry: 生气、愤怒、不满、恼火
- surprised: 惊讶、吃惊、意外、震惊
- thinking: 思考、沉思、疑问、分析
- sleepy: 困倦、疲惫、无聊、懒散
- excited: 兴奋、激动、期待、热情
- confused: 困惑、迷茫、不解、疑惑
- love: 喜爱、喜欢、心动、温柔
- neutral: 平静、中性、正常、客观
- fear: 害怕、担心、紧张、焦虑
- shy: 害羞、腼腆、不好意思

【规则】
1. 只返回一个英文情绪标签，不要其他任何内容
2. 根据文本内容和语气判断最合适的情绪
3. 如果无法判断，返回 neutral

【示例】
输入: 太好了！我很高兴能帮到你！
输出: happy

输入: 这个问题有点复杂，让我想想...
输出: thinking

输入: 哎呀，这样啊...好可惜...
输出: sad"""


def analyze_emotion(text: str) -> str:
    """
    使用 Qwen Turbo 分析文本情绪
    
    参数:
        text: AI 回复的文本内容
    
    返回: 
        情绪标签字符串 (如 "happy", "sad" 等)
    """
    if not text or len(text.strip()) < 2:
        return DEFAULT_EMOTION
    
    try:
        client = _make_client()
        msgs = [
            {"role": "system", "content": EMOTION_EXTRACT_PROMPT},
            {"role": "user", "content": text.strip()[:500]},  # 限制长度
        ]
        rsp = client.chat.completions.create(
            model=os.getenv("QWEN_MODEL", "qwen-turbo"),
            messages=msgs,
            stream=False,
            max_tokens=20,  # 只需要一个词
        )
        emotion = (rsp.choices[0].message.content or "").strip().lower()
        
        # 验证是否是有效情绪
        if emotion in EMOTION_LED_MAP:
            print(f"[EMOTION] Turbo 分析结果: {emotion}", flush=True)
            return emotion
        
        # 尝试模糊匹配
        for valid_emotion in EMOTION_LED_MAP.keys():
            if valid_emotion in emotion:
                print(f"[EMOTION] Turbo 模糊匹配: {valid_emotion} (原始: {emotion})", flush=True)
                return valid_emotion
        
        print(f"[EMOTION] Turbo 返回无效情绪: {emotion}, 使用默认", flush=True)
        return DEFAULT_EMOTION
    except Exception as e:
        print(f"[EMOTION] Turbo 解析失败: {e}", flush=True)
        return DEFAULT_EMOTION


def get_emotion_led_color(emotion: str) -> Tuple[int, int, int, int]:
    """
    获取情绪对应的 LED 颜色配置
    
    返回: (R, G, B, 亮度)
    """
    emotion = emotion.lower()
    return EMOTION_LED_MAP.get(emotion, EMOTION_LED_MAP[DEFAULT_EMOTION])











