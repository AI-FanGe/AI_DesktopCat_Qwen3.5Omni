# emotion_action.py
# -*- coding: utf-8 -*-
"""
情感动作系统：根据情绪生成小马机器人的肢体动作参数

根据情绪类型，为四肢(STS3032)、尾巴、耳朵(PCA9685)生成
随机但合理的运动参数序列。

舵机约束:
- STS3032 腿部 (ID1-4): 0-4095, 中心=2048
  - ID1 左前腿: 高值=前, 低值=后
  - ID2 右前腿: 低值=前, 高值=后  
  - ID3 左后腿: 高值=前, 低值=后
  - ID4 右后腿: 低值=前, 高值=后
- PCA9685 尾巴 (CH13): 0-180, 中心=90, 低=左, 高=右
- PCA9685 左耳 (CH14): 0-180, 中心=90, 低=前/竖, 高=后/垂
- PCA9685 右耳 (CH15): 0-180, 中心=90, 高=前/竖, 低=后/垂
"""

import random
from typing import List, Dict, Any, Optional


# ====================================================================
# 常量
# ====================================================================
LEG_CENTER = 2048
LEG_MIN = 1650
LEG_MAX = 2450

# 各腿的"向前"方向乘数
# ID1(左前): +1, ID2(右前): -1, ID3(左后): +1, ID4(右后): -1
LEG_FWD = [1, -1, 1, -1]

TAIL_CENTER = 90
EAR_L_CENTER = 90
EAR_R_CENTER = 90


def _clamp_leg(pos: int) -> int:
    return max(LEG_MIN, min(LEG_MAX, pos))


def _clamp_angle(val: int, lo: int = 0, hi: int = 180) -> int:
    return max(lo, min(hi, val))


def _leg_pos(leg_idx: int, offset: int) -> int:
    """根据语义偏移量计算腿部位置（正=向前，负=向后）"""
    return _clamp_leg(LEG_CENTER + LEG_FWD[leg_idx] * offset)


# ====================================================================
# 情绪动作配置
# ====================================================================
EMOTION_PROFILES: Dict[str, Dict[str, Any]] = {
    "happy": {
        "name": "开心 - 轻快弹跳，尾巴快速摇，耳朵竖起",
        "frames": (3, 5),
        "duration": (250, 400),
        "leg_offset": (60, 130),
        "leg_speed": (600, 1000),
        "leg_accel": (100, 200),
        "tail_range": (40, 140),
        "ear_l": (40, 70),
        "ear_r": (110, 140),
        "pattern": "bounce",
    },
    "sad": {
        "name": "难过 - 缓慢低沉，尾巴低垂，耳朵向后耷拉",
        "frames": (2, 3),
        "duration": (500, 800),
        "leg_offset": (20, 50),
        "leg_speed": (200, 350),
        "leg_accel": (20, 50),
        "tail_range": (75, 105),
        "ear_l": (95, 115),
        "ear_r": (65, 85),
        "pattern": "droop",
    },
    "angry": {
        "name": "生气 - 跺脚，尾巴紧张摆动，耳朵向后紧贴",
        "frames": (3, 5),
        "duration": (200, 350),
        "leg_offset": (100, 200),
        "leg_speed": (800, 1200),
        "leg_accel": (180, 254),
        "tail_range": (50, 130),
        "ear_l": (110, 135),
        "ear_r": (45, 70),
        "pattern": "stomp",
    },
    "surprised": {
        "name": "惊讶 - 急速后退，高度警觉",
        "frames": (2, 4),
        "duration": (200, 350),
        "leg_offset": (80, 150),
        "leg_speed": (900, 1400),
        "leg_accel": (200, 254),
        "tail_range": (60, 120),
        "ear_l": (25, 50),
        "ear_r": (130, 155),
        "pattern": "startle",
    },
    "thinking": {
        "name": "思考 - 重心偏移，耳朵不对称",
        "frames": (2, 3),
        "duration": (400, 600),
        "leg_offset": (30, 70),
        "leg_speed": (300, 500),
        "leg_accel": (40, 80),
        "tail_range": (70, 110),
        "ear_l": (55, 75),
        "ear_r": (80, 100),
        "pattern": "tilt",
    },
    "sleepy": {
        "name": "困倦 - 松弛缓慢，耳朵松垂",
        "frames": (2, 3),
        "duration": (600, 1000),
        "leg_offset": (15, 35),
        "leg_speed": (150, 250),
        "leg_accel": (15, 35),
        "tail_range": (80, 100),
        "ear_l": (85, 105),
        "ear_r": (75, 95),
        "pattern": "sway",
    },
    "excited": {
        "name": "兴奋 - 欢快小跑，大幅尾摇，耳朵极度前倾",
        "frames": (4, 6),
        "duration": (180, 300),
        "leg_offset": (100, 180),
        "leg_speed": (800, 1200),
        "leg_accel": (150, 254),
        "tail_range": (30, 150),
        "ear_l": (25, 50),
        "ear_r": (130, 155),
        "pattern": "prance",
    },
    "confused": {
        "name": "困惑 - 歪头，耳朵不对称，犹豫移动",
        "frames": (2, 4),
        "duration": (300, 500),
        "leg_offset": (40, 90),
        "leg_speed": (400, 600),
        "leg_accel": (60, 120),
        "tail_range": (65, 115),
        "ear_l": (50, 80),
        "ear_r": (70, 100),
        "pattern": "tilt",
    },
    "love": {
        "name": "喜爱 - 温柔靠近，尾巴轻柔摆动",
        "frames": (3, 5),
        "duration": (300, 500),
        "leg_offset": (40, 100),
        "leg_speed": (400, 700),
        "leg_accel": (80, 150),
        "tail_range": (50, 130),
        "ear_l": (45, 65),
        "ear_r": (115, 135),
        "pattern": "gentle",
    },
    "neutral": {
        "name": "中性 - 平静微晃",
        "frames": (1, 2),
        "duration": (400, 600),
        "leg_offset": (20, 50),
        "leg_speed": (300, 500),
        "leg_accel": (40, 80),
        "tail_range": (75, 105),
        "ear_l": (70, 90),
        "ear_r": (90, 110),
        "pattern": "sway",
    },
    "fear": {
        "name": "害怕 - 颤抖，尾巴夹紧，耳朵向后贴平",
        "frames": (3, 5),
        "duration": (150, 300),
        "leg_offset": (50, 120),
        "leg_speed": (600, 1000),
        "leg_accel": (100, 200),
        "tail_range": (80, 100),
        "ear_l": (110, 130),
        "ear_r": (50, 70),
        "pattern": "tremble",
    },
    "shy": {
        "name": "害羞 - 小幅局促，微微退缩",
        "frames": (2, 3),
        "duration": (350, 550),
        "leg_offset": (30, 70),
        "leg_speed": (300, 500),
        "leg_accel": (50, 100),
        "tail_range": (70, 110),
        "ear_l": (85, 105),
        "ear_r": (75, 95),
        "pattern": "fidget",
    },
    "listening": {
        "name": "倾听 - 耳朵前倾，姿态专注",
        "frames": (1, 2),
        "duration": (600, 1000),
        "leg_offset": (20, 40),
        "leg_speed": (300, 500),
        "leg_accel": (50, 80),
        "tail_range": (80, 100),
        "ear_l": (30, 50),
        "ear_r": (130, 150),
        "pattern": "sway",
    },
}

DEFAULT_PROFILE = EMOTION_PROFILES["neutral"]


# ====================================================================
# 腿部动作模式生成器
# ====================================================================
def _gen_leg_positions(profile: dict, pattern: str) -> List[int]:
    """根据运动模式生成4条腿的目标位置"""
    lo, hi = profile["leg_offset"]

    if pattern == "bounce":
        # 弹跳：所有腿同步微移，产生轻快感
        offset = random.randint(lo, hi) * random.choice([1, -1])
        jitter = 25
        return [_leg_pos(i, offset + random.randint(-jitter, jitter)) for i in range(4)]

    elif pattern == "stomp":
        # 跺脚：随机一条腿大幅前蹬，其他腿轻微移动保持平衡
        positions = [_leg_pos(i, random.randint(-20, 20)) for i in range(4)]
        stomp_leg = random.randint(0, 3)
        stomp_amount = random.randint(lo, hi)
        positions[stomp_leg] = _leg_pos(stomp_leg, stomp_amount)
        return positions

    elif pattern == "startle":
        # 惊吓：快速后退一步
        back_offset = random.randint(lo, hi)
        return [_leg_pos(i, -back_offset + random.randint(-30, 30)) for i in range(4)]

    elif pattern == "prance":
        # 欢快小跑：对角线腿交替，产生活泼感
        offset = random.randint(lo, hi) * random.choice([1, -1])
        phase = random.randint(0, 1)
        positions = []
        for i in range(4):
            if (i % 2) == phase:
                positions.append(_leg_pos(i, offset + random.randint(-20, 20)))
            else:
                positions.append(_leg_pos(i, -offset // 2 + random.randint(-15, 15)))
        return positions

    elif pattern == "tilt":
        # 歪头/重心偏移：前腿和后腿不同方向
        fwd_offset = random.randint(lo // 2, hi)
        side = random.choice([1, -1])
        return [
            _leg_pos(0, side * fwd_offset + random.randint(-15, 15)),
            _leg_pos(1, -side * fwd_offset // 2 + random.randint(-15, 15)),
            _leg_pos(2, -side * fwd_offset // 3 + random.randint(-10, 10)),
            _leg_pos(3, side * fwd_offset // 3 + random.randint(-10, 10)),
        ]

    elif pattern == "droop":
        # 低沉：微微前倾，重心下沉
        fwd = random.randint(lo // 2, lo)
        return [
            _leg_pos(0, fwd + random.randint(-10, 10)),
            _leg_pos(1, fwd + random.randint(-10, 10)),
            _leg_pos(2, -fwd // 2 + random.randint(-10, 10)),
            _leg_pos(3, -fwd // 2 + random.randint(-10, 10)),
        ]

    elif pattern == "tremble":
        # 颤抖：小幅快速随机抖动
        return [_leg_pos(i, random.randint(-lo, lo)) for i in range(4)]

    elif pattern == "fidget":
        # 局促不安：随机小幅移动，偶尔某条腿幅度稍大
        positions = [_leg_pos(i, random.randint(-lo, lo)) for i in range(4)]
        if random.random() < 0.3:
            fidget_leg = random.randint(0, 3)
            positions[fidget_leg] = _leg_pos(fidget_leg, random.randint(lo, hi) * random.choice([1, -1]))
        return positions

    elif pattern == "gentle":
        # 温柔：前腿微微前伸（靠近姿态），后腿保持稳定
        fwd = random.randint(lo, hi)
        return [
            _leg_pos(0, fwd + random.randint(-15, 15)),
            _leg_pos(1, fwd + random.randint(-15, 15)),
            _leg_pos(2, random.randint(-20, 20)),
            _leg_pos(3, random.randint(-20, 20)),
        ]

    else:  # "sway" or default
        # 微晃：轻微随机偏移
        return [_leg_pos(i, random.randint(-lo, lo)) for i in range(4)]


# ====================================================================
# 主接口
# ====================================================================
def generate_emotion_actions(emotion: str) -> List[Dict[str, Any]]:
    """
    根据情绪生成动作帧序列。
    
    参数:
        emotion: 情绪标签 (如 "happy", "sad" 等)
    
    返回:
        动作帧列表，每帧包含:
        - legs: [pos1, pos2, pos3, pos4] 腿部目标位置
        - speed: 腿部速度
        - accel: 腿部加速度
        - tail: 尾巴角度
        - ear_l: 左耳角度
        - ear_r: 右耳角度
        - duration: 持续时间(ms)
    """
    profile = EMOTION_PROFILES.get(emotion.lower(), DEFAULT_PROFILE)

    num_frames = random.randint(*profile["frames"])
    frames = []

    for i in range(num_frames):
        legs = _gen_leg_positions(profile, profile["pattern"])
        speed = random.randint(*profile["leg_speed"])
        accel = random.randint(*profile["leg_accel"])
        tail = random.randint(*profile["tail_range"])
        ear_l = _clamp_angle(random.randint(*profile["ear_l"]))
        ear_r = _clamp_angle(random.randint(*profile["ear_r"]))
        duration = random.randint(*profile["duration"])

        frames.append({
            "legs": legs,
            "speed": speed,
            "accel": accel,
            "tail": tail,
            "ear_l": ear_l,
            "ear_r": ear_r,
            "duration": duration,
        })

    # 最后一帧：缓慢回到中立位置
    frames.append({
        "legs": [LEG_CENTER, LEG_CENTER, LEG_CENTER, LEG_CENTER],
        "speed": random.randint(350, 550),
        "accel": random.randint(50, 90),
        "tail": TAIL_CENTER,
        "ear_l": EAR_L_CENTER,
        "ear_r": EAR_R_CENTER,
        "duration": 400,
    })

    return frames


def format_emact_command(frame: Dict[str, Any]) -> str:
    """
    将动作帧格式化为 EMACT 命令字符串。
    
    格式: EMACT:l1,l2,l3,l4,spd,acc,tail,earL,earR,dur
    """
    legs = frame["legs"]
    return (
        f"EMACT:{legs[0]},{legs[1]},{legs[2]},{legs[3]},"
        f"{frame['speed']},{frame['accel']},"
        f"{frame['tail']},{frame['ear_l']},{frame['ear_r']},"
        f"{frame['duration']}"
    )


async def send_emotion_actions(ws, emotion: str, clear_first: bool = True, tail_locked_angle: Optional[int] = None):
    """
    异步发送情感动作序列到 ESP32。
    
    参数:
        ws: WebSocket 连接
        emotion: 情绪标签
        clear_first: 是否先清空队列
        tail_locked_angle: 如果提供，则锁定尾巴角度（覆盖所有帧的尾巴角度）
    """
    from starlette.websockets import WebSocketState
    
    if not ws or ws.client_state != WebSocketState.CONNECTED:
        return
    
    try:
        if clear_first:
            await ws.send_text("EMACT:CLEAR")
        
        actions = generate_emotion_actions(emotion)
        for action in actions:
            # 如果尾巴被锁定，覆盖所有帧的尾巴角度
            if tail_locked_angle is not None:
                action["tail"] = tail_locked_angle
            cmd = format_emact_command(action)
            await ws.send_text(cmd)
        
        print(f"[EMACT] Sent {len(actions)} frames for emotion '{emotion}'" + 
              (f" (tail locked at {tail_locked_angle}°)" if tail_locked_angle is not None else ""), flush=True)
    except Exception as e:
        print(f"[EMACT] Send failed: {e}", flush=True)











