# voice_command.py
# -*- coding: utf-8 -*-
"""
语音指令模糊匹配 - 从自然语言中识别预设动作指令

在用户与小马对话时，自动检测语句中是否包含动作指令关键词，
若匹配则发送对应的步态命令到ESP32，同时仍然继续AI对话。
"""

from typing import Optional, Tuple


# ====================================================================
# 关键词 → 舵机动作命令映射（非步态类指令）
# (关键词列表, 动作命令字符串, 仅短文本触发)
# 动作命令格式: "SERVO:通道,角度"
# ====================================================================
VOICE_ACTION_MAP = [
    # 充电：尾巴(CH13)转到0度露出充电口
    (["充电", "充电模式", "打开充电", "开始充电", "我要充电", "给你充电"], "SERVO:13,0", False),
    # 结束充电：尾巴(CH13)恢复到90度
    (["结束充电", "充电完成", "充好了", "关闭充电", "停止充电", "充完了"], "SERVO:13,90", False),
]


# ====================================================================
# 关键词 → 步态命令映射
# 按优先级排列：长关键词优先匹配，避免短关键词误匹配
# (关键词列表, GAIT命令, 仅短文本触发)
# ====================================================================
VOICE_COMMAND_MAP = [
    # ---- 多字关键词：无论文本长短都匹配 ----
    # 快走/直线走（优先于普通"走"）
    (["向前走", "直线走", "快走", "走直线", "往前走", "快点走", "走快点", "直着走"], "TROT", False),
    # 跑步（多字优先）
    (["向前跑", "快点跑", "跑快点", "跑步", "快跑", "奔跑", "跑起来", "飞奔"], "RUN", False),
    # 后退
    (["后退", "退后", "往后走", "向后走", "向后退", "倒退", "往后退", "退一步", "往后"], "BACKWARD", False),
    # 效率走（交替对角、无反向力）
    (["效率走", "高效走", "省力走", "省力前进"], "EFFICIENT_WALK", False),
    # 小跑
    (["小跑", "慢跑", "跑跑", "小步跑"], "TROT", False),
    # 慢走
    (["慢走", "走路", "散步", "慢慢走", "走慢点", "溜达"], "WALK", False),
    # 坐下
    (["坐下", "蹲下", "坐好", "请坐", "坐下来", "坐着"], "SIT", False),
    # 倒下
    (["倒下", "躺下", "趴下", "卧倒", "躺倒", "倒下去", "摔倒", "趴着"], "LAYDOWN", False),
    # 跳跃
    (["跳跃", "跳起来", "蹦一下", "跳一下", "蹦跳", "跳一跳", "起跳"], "JUMP", False),
    # 往复
    (["往复", "摆动", "摆摆腿", "晃腿", "动动腿"], "WAVE", False),
    # 待机
    (["站好", "站着", "待机", "休息", "歇一歇", "歇一下", "歇会", "别动了"], "IDLE", False),
    # 停止
    (["停下", "停止", "站住", "别走了", "别跑了", "别动", "不要动"], "STOP", False),
    # 复位
    (["复位", "归位", "站正", "回中"], "CENTER", False),

    # ---- 短关键词：仅在文本较短时触发（避免长句误匹配） ----
    (["跑"], "RUN", True),
    (["走"], "TROT", True),
    (["坐"], "SIT", True),
    (["跳"], "JUMP", True),
    (["蹦"], "JUMP", True),
    (["停"], "STOP", True),
    (["退"], "BACKWARD", True),
]

# 短文本阈值（字符数）：文本 <= 此值时允许短关键词匹配
SHORT_TEXT_THRESHOLD = 10


def match_voice_command(text: str) -> Optional[Tuple[str, str]]:
    """
    从用户语音文本中匹配预设动作指令。

    参数:
        text: 用户语音识别文本

    返回:
        (gait_command, matched_keyword) 如果匹配成功
        None 如果没有匹配
    """
    text = text.strip()
    if not text:
        return None

    is_short = len(text) <= SHORT_TEXT_THRESHOLD

    for keywords, gait_cmd, short_only in VOICE_COMMAND_MAP:
        # 标记为"仅短文本"的规则，长文本时跳过
        if short_only and not is_short:
            continue
        for kw in keywords:
            if kw in text:
                return (gait_cmd, kw)

    return None


def match_voice_action(text: str) -> Optional[Tuple[str, str]]:
    """
    从用户语音文本中匹配非步态类动作指令（如充电）。

    参数:
        text: 用户语音识别文本

    返回:
        (action_command, matched_keyword) 如果匹配成功
        None 如果没有匹配
    """
    text = text.strip()
    if not text:
        return None

    is_short = len(text) <= SHORT_TEXT_THRESHOLD

    for keywords, action_cmd, short_only in VOICE_ACTION_MAP:
        if short_only and not is_short:
            continue
        for kw in keywords:
            if kw in text:
                return (action_cmd, kw)

    return None









