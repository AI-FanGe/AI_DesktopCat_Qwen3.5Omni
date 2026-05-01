# -*- coding: utf-8 -*-
from typing import List, Optional, Tuple


SHORT_TEXT_THRESHOLD = 12


VOICE_TRANSLATE_START_MAP: List[Tuple[List[str], str]] = [
    (
        [
            # 中文触发
            "中英互译", "中英翻译", "英中翻译", "中英同传", "中英同声传译",
            "帮我中英", "中英对照", "把中文翻译成英文", "把英文翻译成中文",
            "翻译成英文", "翻译成英语", "翻译成中文", "中文翻译英文", "英文翻译中文",
            "中文英文翻译", "英文中文翻译",
            # 英文触发
            "chinese english", "english chinese",
            "translate between english and chinese", "translate between chinese and english",
            "translate english to chinese", "translate chinese to english",
            "translate to english", "translate to chinese",
            "english to chinese", "chinese to english",
        ],
        "zh-en",
    ),
]


# 长词：任何句子里出现都直接停止（这些词组本身意图非常明确）
VOICE_TRANSLATE_STOP_KEYWORDS: List[str] = [
    "退出翻译", "结束翻译", "关闭翻译", "停止翻译", "退出同传", "结束同传",
    "不用翻译", "结束翻译模式", "退出翻译模式", "不用翻译了", "不要翻译了",
    "stop translation", "stop translating", "exit translation", "exit translator",
]

# 短词：只有在用户说的整句话很短（口令式）时才触发退出，
# 避免用户要翻译的原文里含这些词被误退出。例如：
# "退出"     —— 短，触发
# "我想退出这个群聊" —— 长，不触发（让 LLM 翻译原文）
VOICE_TRANSLATE_STOP_SHORT_KEYWORDS: List[str] = [
    "退出", "退一下", "退出吧", "退出一下", "退回去", "回去", "回到主界面",
    "回主界面", "回主页", "结束", "结束吧", "结束了", "停一下",
    "不翻了", "不翻译了", "不用了", "算了",
    "exit", "quit", "never mind", "nevermind",
]
# 汉字/英文字符数上限：高于这个长度的整句话里即使出现短词也不算退出，
# 避免"今天下午要取消会议"这种长句里的关键字被误触发。
VOICE_TRANSLATE_STOP_SHORT_MAX_LEN = 8


def match_voice_translation_start(text: str) -> Optional[Tuple[str, str]]:
    text = (text or "").strip()
    if not text:
        return None
    lowered = text.lower()
    for keywords, pair in VOICE_TRANSLATE_START_MAP:
        for keyword in keywords:
            if keyword.lower() in lowered:
                return pair, keyword
    return None


def match_voice_translation_stop(text: str) -> Optional[str]:
    text = (text or "").strip()
    if not text:
        return None
    lowered = text.lower()
    # 1) 长词优先：句子里出现就触发
    for keyword in VOICE_TRANSLATE_STOP_KEYWORDS:
        if keyword.lower() in lowered:
            return keyword
    # 2) 短词：只在口令式短句里触发，避免和"要翻译的内容"冲突
    if len(text) <= VOICE_TRANSLATE_STOP_SHORT_MAX_LEN:
        for keyword in VOICE_TRANSLATE_STOP_SHORT_KEYWORDS:
            if keyword.lower() in lowered:
                return keyword
    return None


VOICE_ACTION_MAP = [
    (["点头", "点一点头"], ["CATPOSE:CLEAR", "CATPOSE:90,72,90,180", "CATPOSE:90,110,90,220", "CATPOSE:90,90,90,180"], False),
    (["摇头", "摇一摇头"], ["CATPOSE:CLEAR", "CATPOSE:58,90,90,180", "CATPOSE:122,90,90,220", "CATPOSE:90,90,90,180"], False),
    (["歪头", "歪一下头", "侧着头"], ["CATPOSE:CLEAR", "CATPOSE:62,98,72,280", "CATPOSE:90,90,90,220"], False),
    (["竖耳朵", "把耳朵竖起来", "认真听"], ["CATPOSE:CLEAR", "CATPOSE:90,84,34,260", "CATPOSE:90,90,90,220"], False),
    (["耷拉耳朵", "耳朵垂下来"], ["CATPOSE:CLEAR", "CATPOSE:90,108,132,340", "CATPOSE:90,90,90,240"], False),
    (["看左边", "向左看"], ["CATPOSE:CLEAR", "CATPOSE:50,92,80,320", "CATPOSE:90,90,90,220"], False),
    (["看右边", "向右看"], ["CATPOSE:CLEAR", "CATPOSE:130,92,80,320", "CATPOSE:90,90,90,220"], False),
]

VOICE_EXPRESSION_MAP = [
    (["开心", "高兴", "笑一个"], "happy", False),
    (["难过", "委屈", "可怜"], "sad", False),
    (["生气", "凶一点"], "angry", False),
    (["害羞", "脸红"], "shy", False),
    (["害怕", "紧张"], "fear", False),
    (["听我说", "认真听", "注意听"], "listening", False),
]


def _is_short(text: str) -> bool:
    return len(text.strip()) <= SHORT_TEXT_THRESHOLD


def match_voice_action(text: str) -> Optional[Tuple[List[str], str]]:
    text = (text or "").strip()
    if not text:
        return None
    short = _is_short(text)
    for keywords, commands, short_only in VOICE_ACTION_MAP:
        if short_only and not short:
            continue
        for keyword in keywords:
            if keyword in text:
                return commands, keyword
    return None


VOICE_VISUAL_REQUEST_KEYWORDS: List[str] = [
    # 中文
    "帮我看一下",
    "帮我看下",
    "帮我看看",
    "你看一下",
    "你看下",
    "你看看",
    "看一下这个",
    "看下这个",
    "看看这个",
    "看看这里",
    "看一下这里",
    "帮我瞅瞅",
    "帮我瞧瞧",
    "帮我观察一下",
    # 英文
    "look at",
    "take a look at",
    "can you look at",
    "could you look at",
    "please look at",
    "what do you see",
    "what can you see",
]


def match_voice_visual_request(text: str) -> Optional[str]:
    text = (text or "").strip()
    if not text:
        return None
    lowered = text.lower()
    for keyword in VOICE_VISUAL_REQUEST_KEYWORDS:
        needle = keyword.lower()
        if needle in lowered:
            return keyword
    return None


def match_voice_expression(text: str) -> Optional[Tuple[str, str]]:
    text = (text or "").strip()
    if not text:
        return None
    short = _is_short(text)
    for keywords, expression, short_only in VOICE_EXPRESSION_MAP:
        if short_only and not short:
            continue
        for keyword in keywords:
            if keyword in text:
                return expression, keyword
    return None
