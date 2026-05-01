"""
马机器人腿部动作编排器
Horse Robot Leg Choreographer

使用CustomTkinter创建时间线界面，用于编排四条腿的动作序列。
舵机ID: 1=左前腿, 2=右前腿, 3=左后腿, 4=右后腿

功能：
- 时间线式关键帧编辑
- 可控制位置、速度、加速度
- 预设步态模板
- 保存/加载动作序列
- 串口实时预览

作者：沙粒云
"""

import customtkinter as ctk
from tkinter import messagebox, filedialog, Canvas
import json
import os
import serial
import serial.tools.list_ports
from dataclasses import dataclass, field, asdict
from typing import List, Dict, Optional
import copy
import threading
import time

# 设置外观
ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

# ===== 数据结构 =====
@dataclass
class Keyframe:
    """关键帧"""
    time: float  # 时间点（秒）
    position: int  # 位置 0-4095
    speed: int  # 速度（时间ms）
    acceleration: int  # 加速度
    
    def to_dict(self):
        return asdict(self)
    
    @staticmethod
    def from_dict(d):
        return Keyframe(**d)

@dataclass
class ServoTrack:
    """舵机轨道"""
    servo_id: int
    name: str
    color: str
    keyframes: List[Keyframe] = field(default_factory=list)
    
    def to_dict(self):
        return {
            "servo_id": self.servo_id,
            "name": self.name,
            "color": self.color,
            "keyframes": [kf.to_dict() for kf in self.keyframes]
        }
    
    @staticmethod
    def from_dict(d):
        return ServoTrack(
            servo_id=d["servo_id"],
            name=d["name"],
            color=d["color"],
            keyframes=[Keyframe.from_dict(kf) for kf in d["keyframes"]]
        )

@dataclass
class GaitPattern:
    """步态模式"""
    name: str
    description: str
    tracks: List[ServoTrack]
    duration: float  # 总时长（秒）
    
    def to_dict(self):
        return {
            "name": self.name,
            "description": self.description,
            "duration": self.duration,
            "tracks": [t.to_dict() for t in self.tracks]
        }
    
    @staticmethod
    def from_dict(d):
        return GaitPattern(
            name=d["name"],
            description=d["description"],
            duration=d["duration"],
            tracks=[ServoTrack.from_dict(t) for t in d["tracks"]]
        )

# ===== 舵机配置 =====
SERVO_CONFIG = [
    {"id": 1, "name": "左前腿", "color": "#FF6B6B"},  # 红色
    {"id": 2, "name": "右前腿", "color": "#4ECDC4"},  # 青色
    {"id": 3, "name": "左后腿", "color": "#FFE66D"},  # 黄色
    {"id": 4, "name": "右后腿", "color": "#95E1D3"},  # 浅绿
]

# 舵机参数范围
POSITION_MIN = 0
POSITION_MAX = 4095
POSITION_CENTER = 2048
SPEED_MIN = 100
SPEED_MAX = 5000
ACCEL_MIN = 0
ACCEL_MAX = 254

# ===== 预设步态 =====

# 马步走路的循环定义（每个舵机的完整周期）
# 总周期2.5秒，四点运动轨迹：
# 前半程(1.5秒): ready→back→front→neutral (向后蓄力再向前推)
# 后半程(1.0秒): neutral→ready (缓缓收回)
# 幅度60%
WALK_GAIT_CONFIG = {
    # 舵机ID: 配置
    # ready: 后方靠前的准备位置（介于back和neutral之间）
    # back: 腿在最后方的位置
    # front: 腿在最前方的位置  
    # neutral: 中立位
    # phase: 相位系数（乘以WALK_PHASE_DELAY得到实际延迟时间）
    3: {"name": "左后腿", "ready": 1898, "back": 1748, "front": 2348, "neutral": 2048, "phase": 0, "color": "#FFE66D"},
    1: {"name": "左前腿", "ready": 2093, "back": 1888, "front": 2408, "neutral": 2198, "phase": 1, "color": "#FF6B6B"},
    4: {"name": "右后腿", "ready": 2198, "back": 2348, "front": 1748, "neutral": 2048, "phase": 2, "color": "#95E1D3"},
    2: {"name": "右前腿", "ready": 2003, "back": 2208, "front": 1688, "neutral": 1898, "phase": 3, "color": "#4ECDC4"},
}

# 周期参数
WALK_CYCLE_TOTAL = 2.5  # 总周期2.5秒

# 前半程(1.5秒)分三段: ready→back→front→neutral
WALK_PHASE_1 = 0.25  # 阶段1：ready→back (蓄力)
WALK_PHASE_2 = 0.85  # 阶段2：back→front (发力推出，距离最长)
WALK_PHASE_3 = 0.4   # 阶段3：front→neutral (回到中立)
# 后半程(1.0秒)
WALK_PHASE_4 = 1.0   # 阶段4：neutral→ready (缓缓收回)

# 加速度参数
WALK_ACCEL_FAST = 250   # 前半程：快速推出，加速度大
WALK_ACCEL_SLOW = 30    # 后半程：缓缓收回，加速度小
WALK_ACCEL_TRANS = 150  # 过渡阶段：用于平滑前后半程的速度变化

# 相位差（秒）
WALK_PHASE_DELAY = 0.625  # 2.5秒周期的1/4 = 0.625秒

# ============ 快走(Trot)步态配置 ============
# 总周期0.9秒，四点运动轨迹：
# 前半程(0.6秒): ready→back→front→neutral (向后蓄力再向前推)
# 后半程(0.3秒): neutral→ready (快速收回)
# 幅度为walk的1.5倍，后腿后摆幅度更大
# 对角步态：左前+右后同时，左后+右前同时，两组相差0.45秒
TROT_GAIT_CONFIG = {
    # 舵机ID: 配置（幅度1.5倍，后腿back更大）
    # phase: 0=左前右后组, 1=左后右前组
    3: {"name": "左后腿", "ready": 1823, "back": 1448, "front": 2498, "neutral": 2048, "phase": 1, "color": "#FFE66D"},  # back更后(1598→1448)
    1: {"name": "左前腿", "ready": 2041, "back": 1733, "front": 2513, "neutral": 2198, "phase": 0, "color": "#FF6B6B"},
    4: {"name": "右后腿", "ready": 2273, "back": 2648, "front": 1598, "neutral": 2048, "phase": 0, "color": "#95E1D3"},  # back更后(2498→2648)
    2: {"name": "右前腿", "ready": 2056, "back": 2363, "front": 1583, "neutral": 1898, "phase": 1, "color": "#4ECDC4"},
}

# 快走周期参数
TROT_CYCLE_TOTAL = 0.9  # 总周期0.9秒

# 前半程(0.6秒)分三段: ready→back→front→neutral
TROT_PHASE_1 = 0.10  # 阶段1：ready→back (蓄力)
TROT_PHASE_2 = 0.35  # 阶段2：back→front (发力推出，距离最长)
TROT_PHASE_3 = 0.15  # 阶段3：front→neutral (回到中立)
# 后半程(0.3秒)
TROT_PHASE_4 = 0.3   # 阶段4：neutral→ready (快速收回)

# 快走加速度参数（更大的加速度以适应更快的节奏）
TROT_ACCEL_FAST = 254   # 前半程：快速推出，最大加速度
TROT_ACCEL_SLOW = 100   # 后半程：快速收回，加速度也较大
TROT_ACCEL_TRANS = 200  # 过渡阶段

# 快走相位差（秒）
TROT_PHASE_DELAY = 0.45  # 半个周期 = 0.45秒

# ============ 跑步(Run)步态配置 ============
# 总周期0.6秒，四点运动轨迹：
# 前半程(0.4秒): ready→back→front→neutral (向后蓄力再向前推)
# 后半程(0.2秒): neutral→ready (快速收回)
# 幅度与trot相同（减小以适应更快节奏）
# 顺序步态：左后→右后→左前→右前，每个相差0.15秒
RUN_GAIT_CONFIG = {
    # 舵机ID: 配置（幅度与trot相同）
    # phase: 0=左后, 1=右后, 2=左前, 3=右前
    3: {"name": "左后腿", "ready": 1823, "back": 1448, "front": 2498, "neutral": 2048, "phase": 0, "color": "#FFE66D"},
    4: {"name": "右后腿", "ready": 2273, "back": 2648, "front": 1598, "neutral": 2048, "phase": 1, "color": "#95E1D3"},
    1: {"name": "左前腿", "ready": 2041, "back": 1733, "front": 2513, "neutral": 2198, "phase": 2, "color": "#FF6B6B"},
    2: {"name": "右前腿", "ready": 2056, "back": 2363, "front": 1583, "neutral": 1898, "phase": 3, "color": "#4ECDC4"},
}

# 跑步周期参数
RUN_CYCLE_TOTAL = 0.6  # 总周期0.6秒

# 前半程(0.4秒)分三段: ready→back→front→neutral
RUN_PHASE_1 = 0.07  # 阶段1：ready→back (蓄力)
RUN_PHASE_2 = 0.23  # 阶段2：back→front (发力推出，距离最长)
RUN_PHASE_3 = 0.10  # 阶段3：front→neutral (回到中立)
# 后半程(0.2秒)
RUN_PHASE_4 = 0.2   # 阶段4：neutral→ready (快速收回)

# 跑步加速度参数（最大加速度以适应最快的节奏）
RUN_ACCEL_FAST = 254   # 前半程：最大加速度
RUN_ACCEL_SLOW = 254   # 后半程：最大加速度
RUN_ACCEL_TRANS = 254  # 过渡阶段：最大加速度

# 跑步相位差（秒）
RUN_PHASE_DELAY = 0.15  # 四分之一周期 = 0.15秒

# ============ 静止待机(Idle)步态配置 ============
# 模拟马自然站立时的状态：
# 1. 四个腿在2048附近微微晃动
# 2. 晃动有相位差，看起来自然
# 3. 偶尔随机抬起一只脚踢几下
IDLE_GAIT_CONFIG = {
    # 舵机ID: 配置
    # base: 基础位置（静止中心点）
    # sway_range: 微晃范围
    # kick_range: 踢腿范围
    3: {"name": "左后腿", "base": 2048, "sway_range": 40, "kick_range": 300, "phase": 0, "color": "#FFE66D"},
    1: {"name": "左前腿", "base": 2048, "sway_range": 35, "kick_range": 350, "phase": 1, "color": "#FF6B6B"},
    4: {"name": "右后腿", "base": 2048, "sway_range": 45, "kick_range": 280, "phase": 2, "color": "#95E1D3"},
    2: {"name": "右前腿", "base": 2048, "sway_range": 38, "kick_range": 320, "phase": 3, "color": "#4ECDC4"},
}

# 静止待机参数
IDLE_SWAY_MIN_TIME = 0.8   # 微晃最短间隔（秒）
IDLE_SWAY_MAX_TIME = 2.5   # 微晃最长间隔（秒）
IDLE_KICK_CHANCE = 0.08    # 每次晃动后踢腿的概率（8%）
IDLE_KICK_COUNT_MIN = 2    # 踢腿最少次数
IDLE_KICK_COUNT_MAX = 5    # 踢腿最多次数

def calc_speed(displacement, time_sec):
    """根据位移和时间计算速度(步/秒)
    STS舵机speed参数单位是步/秒，不是毫秒！
    """
    if time_sec <= 0:
        return 0  # 0 = 最大速度
    return int(abs(displacement) / time_sec)

def create_walk_gait():
    """马步走路 - 四脚交替步态（四点运动）
    
    每个腿完全相同的周期循环（2.5秒）：
    前半程(1.5秒):
    - 阶段1(0.25秒): ready→back，向后蓄力
    - 阶段2(0.85秒): back→front，发力向前推
    - 阶段3(0.4秒): front→neutral，回到中立
    后半程(1.0秒):
    - 阶段4(1.0秒): neutral→ready，缓缓收回准备位
    
    速度根据位移量和时间动态计算（步/秒），确保刚好填满时间
    相位差通过播放逻辑控制
    """
    duration = WALK_CYCLE_TOTAL  # 完整周期2.5秒
    tracks = []
    
    t1 = WALK_PHASE_1
    t2 = t1 + WALK_PHASE_2
    t3 = t2 + WALK_PHASE_3
    
    # 为每个舵机创建循环（用于时间线显示）
    for servo_id, config in WALK_GAIT_CONFIG.items():
        disp_1 = abs(config["back"] - config["ready"])
        disp_2 = abs(config["front"] - config["back"])
        disp_3 = abs(config["neutral"] - config["front"])
        disp_4 = abs(config["ready"] - config["neutral"])
        
        speed_1 = calc_speed(disp_1, WALK_PHASE_1)
        speed_2 = calc_speed(disp_2, WALK_PHASE_2)
        speed_3 = calc_speed(disp_3, WALK_PHASE_3)
        speed_4 = calc_speed(disp_4, WALK_PHASE_4)
        
        tracks.append(ServoTrack(
            servo_id=servo_id, 
            name=config["name"], 
            color=config["color"],
            keyframes=[
                Keyframe(time=0.0, position=config["ready"], speed=speed_1, acceleration=WALK_ACCEL_FAST),
                Keyframe(time=t1, position=config["back"], speed=speed_2, acceleration=WALK_ACCEL_FAST),
                Keyframe(time=t2, position=config["front"], speed=speed_3, acceleration=WALK_ACCEL_FAST),
                Keyframe(time=t3, position=config["neutral"], speed=speed_4, acceleration=WALK_ACCEL_SLOW),
                Keyframe(time=WALK_CYCLE_TOTAL, position=config["ready"], speed=speed_1, acceleration=WALK_ACCEL_FAST),
            ]
        ))
    
    # 按舵机ID排序
    tracks.sort(key=lambda t: t.servo_id)
    
    return GaitPattern(
        name="马步走路",
        description=f"四脚交替步态，2.5秒周期(蓄力推出1.5s+收回1.0s)，幅度60%",
        duration=duration,
        tracks=tracks
    )

def create_trot_gait():
    """快走 - 对角步态（四点运动，1.5倍幅度）
    
    每个腿完全相同的周期循环（1.5秒）：
    前半程(0.9秒):
    - 阶段1(0.15秒): ready→back，向后蓄力
    - 阶段2(0.50秒): back→front，发力向前推
    - 阶段3(0.25秒): front→neutral，回到中立
    后半程(0.6秒):
    - 阶段4(0.6秒): neutral→ready，快速收回准备位
    
    对角步态：左前+右后同时，左后+右前同时，两组相差0.75秒
    """
    duration = TROT_CYCLE_TOTAL  # 完整周期1.5秒
    tracks = []
    
    t1 = TROT_PHASE_1
    t2 = t1 + TROT_PHASE_2
    t3 = t2 + TROT_PHASE_3
    
    # 为每个舵机创建循环（用于时间线显示）
    for servo_id, config in TROT_GAIT_CONFIG.items():
        disp_1 = abs(config["back"] - config["ready"])
        disp_2 = abs(config["front"] - config["back"])
        disp_3 = abs(config["neutral"] - config["front"])
        disp_4 = abs(config["ready"] - config["neutral"])
        
        speed_1 = calc_speed(disp_1, TROT_PHASE_1)
        speed_2 = calc_speed(disp_2, TROT_PHASE_2)
        speed_3 = calc_speed(disp_3, TROT_PHASE_3)
        speed_4 = calc_speed(disp_4, TROT_PHASE_4)
        
        tracks.append(ServoTrack(
            servo_id=servo_id, 
            name=config["name"], 
            color=config["color"],
            keyframes=[
                Keyframe(time=0.0, position=config["ready"], speed=speed_1, acceleration=TROT_ACCEL_FAST),
                Keyframe(time=t1, position=config["back"], speed=speed_2, acceleration=TROT_ACCEL_FAST),
                Keyframe(time=t2, position=config["front"], speed=speed_3, acceleration=TROT_ACCEL_FAST),
                Keyframe(time=t3, position=config["neutral"], speed=speed_4, acceleration=TROT_ACCEL_TRANS),
                Keyframe(time=TROT_CYCLE_TOTAL, position=config["ready"], speed=speed_1, acceleration=TROT_ACCEL_FAST),
            ]
        ))
    
    # 按舵机ID排序
    tracks.sort(key=lambda t: t.servo_id)
    
    return GaitPattern(
        name="快走",
        description=f"对角步态，0.9秒周期(推出0.6s+收回0.3s)，幅度1.5倍，后腿后摆更大",
        duration=duration,
        tracks=tracks
    )

def create_run_gait():
    """跑步 - 顺序步态（四点运动，幅度与trot相同）
    
    每个腿完全相同的周期循环（0.6秒）：
    前半程(0.4秒):
    - 阶段1(0.07秒): ready→back，向后蓄力
    - 阶段2(0.23秒): back→front，发力向前推
    - 阶段3(0.10秒): front→neutral，回到中立
    后半程(0.2秒):
    - 阶段4(0.2秒): neutral→ready，快速收回准备位
    
    顺序步态：左后→右后→左前→右前，每个相差0.15秒
    """
    duration = RUN_CYCLE_TOTAL  # 完整周期0.6秒
    tracks = []
    
    t1 = RUN_PHASE_1
    t2 = t1 + RUN_PHASE_2
    t3 = t2 + RUN_PHASE_3
    
    # 为每个舵机创建循环（用于时间线显示）
    for servo_id, config in RUN_GAIT_CONFIG.items():
        disp_1 = abs(config["back"] - config["ready"])
        disp_2 = abs(config["front"] - config["back"])
        disp_3 = abs(config["neutral"] - config["front"])
        disp_4 = abs(config["ready"] - config["neutral"])
        
        speed_1 = calc_speed(disp_1, RUN_PHASE_1)
        speed_2 = calc_speed(disp_2, RUN_PHASE_2)
        speed_3 = calc_speed(disp_3, RUN_PHASE_3)
        speed_4 = calc_speed(disp_4, RUN_PHASE_4)
        
        tracks.append(ServoTrack(
            servo_id=servo_id, 
            name=config["name"], 
            color=config["color"],
            keyframes=[
                Keyframe(time=0.0, position=config["ready"], speed=speed_1, acceleration=RUN_ACCEL_FAST),
                Keyframe(time=t1, position=config["back"], speed=speed_2, acceleration=RUN_ACCEL_FAST),
                Keyframe(time=t2, position=config["front"], speed=speed_3, acceleration=RUN_ACCEL_FAST),
                Keyframe(time=t3, position=config["neutral"], speed=speed_4, acceleration=RUN_ACCEL_TRANS),
                Keyframe(time=RUN_CYCLE_TOTAL, position=config["ready"], speed=speed_1, acceleration=RUN_ACCEL_FAST),
            ]
        ))
    
    # 按舵机ID排序
    tracks.sort(key=lambda t: t.servo_id)
    
    return GaitPattern(
        name="跑步",
        description=f"顺序步态，0.6秒周期(推出0.4s+收回0.2s)，幅度与trot相同",
        duration=duration,
        tracks=tracks
    )

def create_gallop_gait():
    """跑步 - 疾驰步态"""
    duration = 0.6
    tracks = []
    
    # 疾驰：前腿几乎同时着地，后腿几乎同时着地
    tracks.append(ServoTrack(
        servo_id=1, name="左前腿", color="#FF6B6B",
        keyframes=[
            Keyframe(time=0.0, position=1000, speed=200, acceleration=150),
            Keyframe(time=0.15, position=3000, speed=200, acceleration=150),
            Keyframe(time=0.3, position=1000, speed=200, acceleration=150),
            Keyframe(time=0.45, position=3000, speed=200, acceleration=150),
            Keyframe(time=0.6, position=1000, speed=200, acceleration=150),
        ]
    ))
    # 右前腿（与左前腿有微小相位差）
    tracks.append(ServoTrack(
        servo_id=2, name="右前腿", color="#4ECDC4",
        keyframes=[
            Keyframe(time=0.0, position=1200, speed=200, acceleration=150),
            Keyframe(time=0.15, position=2800, speed=200, acceleration=150),
            Keyframe(time=0.3, position=1200, speed=200, acceleration=150),
            Keyframe(time=0.45, position=2800, speed=200, acceleration=150),
            Keyframe(time=0.6, position=1200, speed=200, acceleration=150),
        ]
    ))
    # 左后腿（与前腿有相位差）
    tracks.append(ServoTrack(
        servo_id=3, name="左后腿", color="#FFE66D",
        keyframes=[
            Keyframe(time=0.0, position=2800, speed=200, acceleration=150),
            Keyframe(time=0.15, position=1200, speed=200, acceleration=150),
            Keyframe(time=0.3, position=2800, speed=200, acceleration=150),
            Keyframe(time=0.45, position=1200, speed=200, acceleration=150),
            Keyframe(time=0.6, position=2800, speed=200, acceleration=150),
        ]
    ))
    tracks.append(ServoTrack(
        servo_id=4, name="右后腿", color="#95E1D3",
        keyframes=[
            Keyframe(time=0.0, position=3000, speed=200, acceleration=150),
            Keyframe(time=0.15, position=1000, speed=200, acceleration=150),
            Keyframe(time=0.3, position=3000, speed=200, acceleration=150),
            Keyframe(time=0.45, position=1000, speed=200, acceleration=150),
            Keyframe(time=0.6, position=3000, speed=200, acceleration=150),
        ]
    ))
    
    return GaitPattern(
        name="跑步",
        description="疾驰步态，最快速度，前后腿成组运动",
        duration=duration,
        tracks=tracks
    )

# ===== 时间线画布 =====
class TimelineCanvas(ctk.CTkFrame):
    """时间线画布"""
    
    def __init__(self, parent, app, **kwargs):
        super().__init__(parent, **kwargs)
        
        self.app = app
        self.tracks: List[ServoTrack] = []
        self.duration = 2.0  # 默认2秒
        self.zoom = 1.0
        self.scroll_x = 0
        
        # 选中状态
        self.selected_keyframe: Optional[tuple] = None  # (track_idx, kf_idx)
        self.dragging = False
        self.drag_start_x = 0
        
        # 画布尺寸
        self.track_height = 80
        self.header_width = 120
        self.time_ruler_height = 40
        self.keyframe_radius = 10
        
        # 播放头
        self.playhead_time = 0.0
        self.is_playing = False
        
        # 创建画布
        self.canvas = Canvas(
            self, 
            bg="#1a1a2e", 
            highlightthickness=0,
            width=1000,
            height=400
        )
        self.canvas.pack(fill="both", expand=True, padx=5, pady=5)
        
        # 绑定事件
        self.canvas.bind("<Button-1>", self.on_click)
        self.canvas.bind("<B1-Motion>", self.on_drag)
        self.canvas.bind("<ButtonRelease-1>", self.on_release)
        self.canvas.bind("<Double-Button-1>", self.on_double_click)
        self.canvas.bind("<Button-3>", self.on_right_click)
        self.canvas.bind("<Configure>", self.on_resize)
        self.canvas.bind("<MouseWheel>", self.on_scroll)
        
    def set_tracks(self, tracks: List[ServoTrack], duration: float):
        """设置轨道"""
        self.tracks = tracks
        self.duration = duration
        self.selected_keyframe = None
        self.redraw()
        
    def redraw(self):
        """重绘时间线"""
        self.canvas.delete("all")
        
        canvas_width = self.canvas.winfo_width()
        canvas_height = self.canvas.winfo_height()
        
        if canvas_width < 10:
            canvas_width = 1000
        
        # 计算时间轴宽度
        timeline_width = canvas_width - self.header_width
        pixels_per_second = (timeline_width / self.duration) * self.zoom
        
        # 绘制背景网格
        self._draw_grid(timeline_width, pixels_per_second)
        
        # 绘制时间标尺
        self._draw_time_ruler(timeline_width, pixels_per_second)
        
        # 绘制轨道
        for i, track in enumerate(self.tracks):
            self._draw_track(i, track, timeline_width, pixels_per_second)
        
        # 绘制播放头
        self._draw_playhead(pixels_per_second)
        
    def _draw_grid(self, timeline_width, pps):
        """绘制背景网格"""
        # 垂直线（时间分割）
        interval = 0.1  # 0.1秒间隔
        if pps < 50:
            interval = 0.5
        if pps < 20:
            interval = 1.0
            
        t = 0
        while t <= self.duration:
            x = self.header_width + t * pps - self.scroll_x
            if 0 <= x <= self.header_width + timeline_width:
                color = "#2d2d44" if (t * 10) % 5 != 0 else "#3d3d5c"
                self.canvas.create_line(
                    x, self.time_ruler_height, 
                    x, self.time_ruler_height + len(self.tracks) * self.track_height,
                    fill=color, width=1
                )
            t += interval
            
        # 水平线（轨道分割）
        for i in range(len(self.tracks) + 1):
            y = self.time_ruler_height + i * self.track_height
            self.canvas.create_line(
                0, y, self.header_width + timeline_width, y,
                fill="#3d3d5c", width=1
            )
            
    def _draw_time_ruler(self, timeline_width, pps):
        """绘制时间标尺"""
        # 背景
        self.canvas.create_rectangle(
            self.header_width, 0, 
            self.header_width + timeline_width, self.time_ruler_height,
            fill="#16213e", outline=""
        )
        
        # 时间刻度
        interval = 0.5
        if pps < 100:
            interval = 1.0
            
        t = 0
        while t <= self.duration:
            x = self.header_width + t * pps - self.scroll_x
            if self.header_width <= x <= self.header_width + timeline_width:
                # 刻度线
                self.canvas.create_line(
                    x, self.time_ruler_height - 10,
                    x, self.time_ruler_height,
                    fill="#6c5ce7", width=2
                )
                # 时间文字
                self.canvas.create_text(
                    x, self.time_ruler_height - 20,
                    text=f"{t:.1f}s",
                    fill="#a29bfe",
                    font=("Consolas", 10)
                )
            t += interval
            
    def _draw_track(self, index, track: ServoTrack, timeline_width, pps):
        """绘制单个轨道"""
        y_start = self.time_ruler_height + index * self.track_height
        y_center = y_start + self.track_height // 2
        
        # 轨道标题背景
        self.canvas.create_rectangle(
            0, y_start, 
            self.header_width, y_start + self.track_height,
            fill="#0f3460", outline=""
        )
        
        # 颜色标识
        self.canvas.create_rectangle(
            0, y_start, 
            5, y_start + self.track_height,
            fill=track.color, outline=""
        )
        
        # 轨道名称
        self.canvas.create_text(
            60, y_center,
            text=f"ID{track.servo_id}: {track.name}",
            fill="white",
            font=("Microsoft YaHei", 11, "bold")
        )
        
        # 轨道背景
        self.canvas.create_rectangle(
            self.header_width, y_start,
            self.header_width + timeline_width, y_start + self.track_height,
            fill="#1a1a2e", outline=""
        )
        
        # 绘制关键帧连线
        if len(track.keyframes) > 1:
            points = []
            for kf in track.keyframes:
                x = self.header_width + kf.time * pps - self.scroll_x
                # 根据位置计算y（可视化位置）
                pos_ratio = (kf.position - POSITION_MIN) / (POSITION_MAX - POSITION_MIN)
                y = y_start + self.track_height - 10 - pos_ratio * (self.track_height - 20)
                points.extend([x, y])
            
            if len(points) >= 4:
                self.canvas.create_line(
                    points, 
                    fill=track.color, 
                    width=2, 
                    smooth=True
                )
        
        # 绘制关键帧
        for kf_idx, kf in enumerate(track.keyframes):
            x = self.header_width + kf.time * pps - self.scroll_x
            pos_ratio = (kf.position - POSITION_MIN) / (POSITION_MAX - POSITION_MIN)
            y = y_start + self.track_height - 10 - pos_ratio * (self.track_height - 20)
            
            # 判断是否选中
            is_selected = (self.selected_keyframe == (index, kf_idx))
            
            # 绘制关键帧圆点
            radius = self.keyframe_radius + (3 if is_selected else 0)
            outline_color = "#ffffff" if is_selected else track.color
            outline_width = 3 if is_selected else 2
            
            self.canvas.create_oval(
                x - radius, y - radius,
                x + radius, y + radius,
                fill=track.color,
                outline=outline_color,
                width=outline_width,
                tags=f"kf_{index}_{kf_idx}"
            )
            
            # 显示位置值
            self.canvas.create_text(
                x, y - radius - 8,
                text=str(kf.position),
                fill="#ffffff",
                font=("Consolas", 8)
            )
            
    def _draw_playhead(self, pps):
        """绘制播放头"""
        x = self.header_width + self.playhead_time * pps - self.scroll_x
        height = self.time_ruler_height + len(self.tracks) * self.track_height
        
        # 播放头线
        self.canvas.create_line(
            x, 0, x, height,
            fill="#e74c3c", width=2
        )
        
        # 播放头顶部三角形
        self.canvas.create_polygon(
            x - 8, 0,
            x + 8, 0,
            x, 15,
            fill="#e74c3c", outline=""
        )
        
    def on_click(self, event):
        """点击事件"""
        # 检查是否点击了关键帧
        canvas_width = self.canvas.winfo_width()
        timeline_width = canvas_width - self.header_width
        pps = (timeline_width / self.duration) * self.zoom
        
        for track_idx, track in enumerate(self.tracks):
            y_start = self.time_ruler_height + track_idx * self.track_height
            
            for kf_idx, kf in enumerate(track.keyframes):
                x = self.header_width + kf.time * pps - self.scroll_x
                pos_ratio = (kf.position - POSITION_MIN) / (POSITION_MAX - POSITION_MIN)
                y = y_start + self.track_height - 10 - pos_ratio * (self.track_height - 20)
                
                # 检查点击距离
                dist = ((event.x - x) ** 2 + (event.y - y) ** 2) ** 0.5
                if dist <= self.keyframe_radius + 5:
                    self.selected_keyframe = (track_idx, kf_idx)
                    self.dragging = True
                    self.drag_start_x = event.x
                    self.app.on_keyframe_selected(track_idx, kf_idx)
                    self.redraw()
                    return
        
        # 点击时间线区域 - 移动播放头
        if event.x > self.header_width and event.y < self.time_ruler_height:
            self.playhead_time = (event.x - self.header_width + self.scroll_x) / pps
            self.playhead_time = max(0, min(self.duration, self.playhead_time))
            self.redraw()
            
        self.selected_keyframe = None
        self.redraw()
        
    def on_drag(self, event):
        """拖动事件"""
        if not self.dragging or self.selected_keyframe is None:
            return
            
        track_idx, kf_idx = self.selected_keyframe
        track = self.tracks[track_idx]
        kf = track.keyframes[kf_idx]
        
        canvas_width = self.canvas.winfo_width()
        timeline_width = canvas_width - self.header_width
        pps = (timeline_width / self.duration) * self.zoom
        
        # 计算新时间
        new_time = (event.x - self.header_width + self.scroll_x) / pps
        new_time = max(0, min(self.duration, new_time))
        
        # 计算新位置（基于Y坐标）
        y_start = self.time_ruler_height + track_idx * self.track_height
        y_relative = event.y - y_start
        pos_ratio = 1 - (y_relative - 10) / (self.track_height - 20)
        pos_ratio = max(0, min(1, pos_ratio))
        new_position = int(POSITION_MIN + pos_ratio * (POSITION_MAX - POSITION_MIN))
        
        # 更新关键帧
        kf.time = round(new_time, 2)
        kf.position = new_position
        
        # 更新属性面板
        self.app.on_keyframe_selected(track_idx, kf_idx)
        self.redraw()
        
    def on_release(self, event):
        """释放事件"""
        self.dragging = False
        # 按时间排序关键帧
        if self.selected_keyframe:
            track_idx, _ = self.selected_keyframe
            self.tracks[track_idx].keyframes.sort(key=lambda kf: kf.time)
            # 重新查找选中的关键帧位置
            self.redraw()
            
    def on_double_click(self, event):
        """双击添加关键帧"""
        if event.x <= self.header_width:
            return
            
        canvas_width = self.canvas.winfo_width()
        timeline_width = canvas_width - self.header_width
        pps = (timeline_width / self.duration) * self.zoom
        
        # 确定是哪个轨道
        track_idx = (event.y - self.time_ruler_height) // self.track_height
        if 0 <= track_idx < len(self.tracks):
            # 计算时间和位置
            new_time = (event.x - self.header_width + self.scroll_x) / pps
            new_time = round(max(0, min(self.duration, new_time)), 2)
            
            y_start = self.time_ruler_height + track_idx * self.track_height
            y_relative = event.y - y_start
            pos_ratio = 1 - (y_relative - 10) / (self.track_height - 20)
            pos_ratio = max(0, min(1, pos_ratio))
            new_position = int(POSITION_MIN + pos_ratio * (POSITION_MAX - POSITION_MIN))
            
            # 添加新关键帧
            new_kf = Keyframe(
                time=new_time,
                position=new_position,
                speed=500,
                acceleration=50
            )
            self.tracks[track_idx].keyframes.append(new_kf)
            self.tracks[track_idx].keyframes.sort(key=lambda kf: kf.time)
            
            # 选中新关键帧
            new_idx = self.tracks[track_idx].keyframes.index(new_kf)
            self.selected_keyframe = (track_idx, new_idx)
            self.app.on_keyframe_selected(track_idx, new_idx)
            
            self.redraw()
            
    def on_right_click(self, event):
        """右键删除关键帧"""
        canvas_width = self.canvas.winfo_width()
        timeline_width = canvas_width - self.header_width
        pps = (timeline_width / self.duration) * self.zoom
        
        for track_idx, track in enumerate(self.tracks):
            y_start = self.time_ruler_height + track_idx * self.track_height
            
            for kf_idx, kf in enumerate(track.keyframes):
                x = self.header_width + kf.time * pps - self.scroll_x
                pos_ratio = (kf.position - POSITION_MIN) / (POSITION_MAX - POSITION_MIN)
                y = y_start + self.track_height - 10 - pos_ratio * (self.track_height - 20)
                
                dist = ((event.x - x) ** 2 + (event.y - y) ** 2) ** 0.5
                if dist <= self.keyframe_radius + 5:
                    # 删除关键帧
                    if len(track.keyframes) > 1:
                        track.keyframes.pop(kf_idx)
                        self.selected_keyframe = None
                        self.redraw()
                    else:
                        messagebox.showwarning("提示", "每个轨道至少保留一个关键帧")
                    return
                    
    def on_resize(self, event):
        """窗口大小改变"""
        self.redraw()
        
    def on_scroll(self, event):
        """滚轮缩放"""
        if event.delta > 0:
            self.zoom = min(4.0, self.zoom * 1.1)
        else:
            self.zoom = max(0.5, self.zoom / 1.1)
        self.redraw()
        
    def set_playhead(self, time_pos):
        """设置播放头位置"""
        self.playhead_time = time_pos
        self.redraw()

# ===== 属性编辑器 =====
class PropertyEditor(ctk.CTkFrame):
    """关键帧属性编辑器"""
    
    def __init__(self, parent, app, **kwargs):
        super().__init__(parent, **kwargs)
        
        self.app = app
        self.current_track_idx = None
        self.current_kf_idx = None
        
        # 初始化存储字典
        self.entries = {}
        self.sliders = {}
        
        # 标题
        self.title_label = ctk.CTkLabel(
            self, 
            text="关键帧属性", 
            font=("Microsoft YaHei", 16, "bold")
        )
        self.title_label.pack(pady=10)
        
        # 信息标签
        self.info_label = ctk.CTkLabel(
            self, 
            text="选择一个关键帧进行编辑",
            font=("Microsoft YaHei", 12),
            text_color="#888888"
        )
        self.info_label.pack(pady=5)
        
        # 属性容器
        self.props_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.props_frame.pack(fill="x", padx=20, pady=10)
        
        # 时间
        self._create_property_row(self.props_frame, 0, "时间 (秒)", "time", 0, 10, 0.01)
        
        # 位置
        self._create_property_row(self.props_frame, 1, "位置", "position", POSITION_MIN, POSITION_MAX, 1)
        
        # 速度
        self._create_property_row(self.props_frame, 2, "速度 (ms)", "speed", SPEED_MIN, SPEED_MAX, 10)
        
        # 加速度
        self._create_property_row(self.props_frame, 3, "加速度", "acceleration", ACCEL_MIN, ACCEL_MAX, 1)
        
        # 快捷操作
        self.quick_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.quick_frame.pack(fill="x", padx=20, pady=10)
        
        ctk.CTkLabel(
            self.quick_frame, 
            text="快捷位置",
            font=("Microsoft YaHei", 12)
        ).pack(pady=5)
        
        quick_btn_frame = ctk.CTkFrame(self.quick_frame, fg_color="transparent")
        quick_btn_frame.pack()
        
        ctk.CTkButton(
            quick_btn_frame, 
            text="最小", 
            width=60,
            command=lambda: self._set_position(POSITION_MIN)
        ).pack(side="left", padx=2)
        
        ctk.CTkButton(
            quick_btn_frame, 
            text="中间", 
            width=60,
            command=lambda: self._set_position(POSITION_CENTER)
        ).pack(side="left", padx=2)
        
        ctk.CTkButton(
            quick_btn_frame, 
            text="最大", 
            width=60,
            command=lambda: self._set_position(POSITION_MAX)
        ).pack(side="left", padx=2)
        
        # 删除按钮
        self.delete_btn = ctk.CTkButton(
            self, 
            text="🗑️ 删除关键帧", 
            fg_color="#e74c3c",
            hover_color="#c0392b",
            command=self._delete_keyframe
        )
        self.delete_btn.pack(pady=20)
        
    def _create_property_row(self, parent, row, label_text, prop_name, min_val, max_val, step):
        """创建属性行"""
        frame = ctk.CTkFrame(parent, fg_color="transparent")
        frame.pack(fill="x", pady=5)
        
        # 标签
        label = ctk.CTkLabel(frame, text=label_text, width=80)
        label.pack(side="left")
        
        # 滑块
        slider = ctk.CTkSlider(
            frame, 
            from_=min_val, 
            to=max_val,
            width=150,
            command=lambda v, pn=prop_name: self._on_slider_change(pn, v)
        )
        slider.pack(side="left", padx=5)
        slider.set(min_val)
        
        # 输入框
        entry = ctk.CTkEntry(frame, width=80)
        entry.pack(side="left", padx=5)
        entry.bind("<Return>", lambda e, pn=prop_name: self._on_entry_change(pn))
        entry.bind("<FocusOut>", lambda e, pn=prop_name: self._on_entry_change(pn))
        
        self.sliders[prop_name] = slider
        self.entries[prop_name] = entry
        
    def _on_slider_change(self, prop_name, value):
        """滑块变化"""
        if self.current_track_idx is None:
            return
            
        kf = self.app.timeline.tracks[self.current_track_idx].keyframes[self.current_kf_idx]
        
        if prop_name == "time":
            kf.time = round(value, 2)
        elif prop_name == "position":
            kf.position = int(value)
        elif prop_name == "speed":
            kf.speed = int(value)
        elif prop_name == "acceleration":
            kf.acceleration = int(value)
            
        # 更新输入框
        self.entries[prop_name].delete(0, "end")
        if prop_name == "time":
            self.entries[prop_name].insert(0, f"{value:.2f}")
        else:
            self.entries[prop_name].insert(0, str(int(value)))
            
        self.app.timeline.redraw()
        
    def _on_entry_change(self, prop_name):
        """输入框变化"""
        if self.current_track_idx is None:
            return
            
        try:
            value = float(self.entries[prop_name].get())
            kf = self.app.timeline.tracks[self.current_track_idx].keyframes[self.current_kf_idx]
            
            if prop_name == "time":
                kf.time = round(max(0, min(self.app.duration, value)), 2)
                self.sliders[prop_name].set(kf.time)
            elif prop_name == "position":
                kf.position = int(max(POSITION_MIN, min(POSITION_MAX, value)))
                self.sliders[prop_name].set(kf.position)
            elif prop_name == "speed":
                kf.speed = int(max(SPEED_MIN, min(SPEED_MAX, value)))
                self.sliders[prop_name].set(kf.speed)
            elif prop_name == "acceleration":
                kf.acceleration = int(max(ACCEL_MIN, min(ACCEL_MAX, value)))
                self.sliders[prop_name].set(kf.acceleration)
                
            self.app.timeline.redraw()
        except ValueError:
            pass
            
    def _set_position(self, pos):
        """设置位置快捷按钮"""
        if self.current_track_idx is None:
            return
            
        kf = self.app.timeline.tracks[self.current_track_idx].keyframes[self.current_kf_idx]
        kf.position = pos
        
        self.sliders["position"].set(pos)
        self.entries["position"].delete(0, "end")
        self.entries["position"].insert(0, str(pos))
        
        self.app.timeline.redraw()
        
    def _delete_keyframe(self):
        """删除关键帧"""
        if self.current_track_idx is None:
            return
            
        track = self.app.timeline.tracks[self.current_track_idx]
        if len(track.keyframes) <= 1:
            messagebox.showwarning("提示", "每个轨道至少保留一个关键帧")
            return
            
        track.keyframes.pop(self.current_kf_idx)
        self.current_track_idx = None
        self.current_kf_idx = None
        self.info_label.configure(text="选择一个关键帧进行编辑")
        self.app.timeline.selected_keyframe = None
        self.app.timeline.redraw()
        
    def set_keyframe(self, track_idx, kf_idx):
        """设置当前编辑的关键帧"""
        self.current_track_idx = track_idx
        self.current_kf_idx = kf_idx
        
        track = self.app.timeline.tracks[track_idx]
        kf = track.keyframes[kf_idx]
        
        self.info_label.configure(text=f"{track.name} - 关键帧 {kf_idx + 1}")
        
        # 更新控件值
        self.sliders["time"].set(kf.time)
        self.entries["time"].delete(0, "end")
        self.entries["time"].insert(0, f"{kf.time:.2f}")
        
        self.sliders["position"].set(kf.position)
        self.entries["position"].delete(0, "end")
        self.entries["position"].insert(0, str(kf.position))
        
        self.sliders["speed"].set(kf.speed)
        self.entries["speed"].delete(0, "end")
        self.entries["speed"].insert(0, str(kf.speed))
        
        self.sliders["acceleration"].set(kf.acceleration)
        self.entries["acceleration"].delete(0, "end")
        self.entries["acceleration"].insert(0, str(kf.acceleration))

# ===== HTTP 控制器 =====
import urllib.request
import urllib.error

class HTTPController:
    """HTTP控制器 - 通过WiFi控制ESP32"""
    
    def __init__(self):
        self.esp32_ip = "192.168.2.72"  # 默认IP
        self.connected = False
        self.timeout = 1.0  # 1秒超时（增加容错）
        self.send_errors = 0  # 发送错误计数
        
    def set_ip(self, ip: str):
        """设置ESP32 IP地址"""
        self.esp32_ip = ip.strip()
        
    def test_connection(self) -> bool:
        """测试连接"""
        try:
            url = f"http://{self.esp32_ip}/status"
            req = urllib.request.Request(url, method='GET')
            with urllib.request.urlopen(req, timeout=2) as response:
                self.connected = True
                return True
        except:
            self.connected = False
            return False
            
    def connect(self) -> bool:
        """连接（测试连通性）"""
        return self.test_connection()
        
    def disconnect(self):
        """断开连接"""
        self.connected = False
        
    def send_servo_command(self, servo_id: int, position: int, speed: int, acceleration: int, silent: bool = False) -> bool:
        """发送舵机命令
        
        Args:
            silent: 静默模式，不打印错误（用于播放循环）
        """
        if not self.connected:
            return False
            
        try:
            # 使用HTTP GET请求
            url = f"http://{self.esp32_ip}/sts?id={servo_id}&pos={position}&speed={speed}&accel={acceleration}"
            req = urllib.request.Request(url, method='GET')
            with urllib.request.urlopen(req, timeout=self.timeout) as response:
                self.send_errors = 0  # 重置错误计数
                return response.status == 200
        except Exception as e:
            self.send_errors += 1
            if not silent and self.send_errors <= 3:  # 只打印前3次错误
                print(f"发送命令失败: {e}")
            return False
            
    def scan_servos(self) -> str:
        """扫描舵机"""
        try:
            url = f"http://{self.esp32_ip}/sts?scan=1"
            req = urllib.request.Request(url, method='GET')
            with urllib.request.urlopen(req, timeout=5) as response:
                return response.read().decode('utf-8')
        except Exception as e:
            return f"扫描失败: {e}"


# ===== 串口控制器（保留作为备选） =====
class SerialController:
    """串口控制器"""
    
    def __init__(self):
        self.serial_port: Optional[serial.Serial] = None
        self.connected = False
        
    def get_ports(self):
        """获取可用串口列表"""
        ports = serial.tools.list_ports.comports()
        return [p.device for p in ports]
        
    def connect(self, port, baudrate=1000000):
        """连接串口"""
        try:
            self.serial_port = serial.Serial(port, baudrate, timeout=1)
            self.connected = True
            return True
        except Exception as e:
            print(f"串口连接失败: {e}")
            return False
            
    def disconnect(self):
        """断开串口"""
        if self.serial_port:
            self.serial_port.close()
            self.connected = False
            
    def send_servo_command(self, servo_id, position, speed, acceleration):
        """发送舵机命令"""
        if not self.connected:
            return False
            
        # 格式: STS:id,pos,speed,accel
        cmd = f"STS:{servo_id},{position},{speed},{acceleration}\n"
        try:
            self.serial_port.write(cmd.encode())
            return True
        except:
            return False

# ===== 主应用 =====
class LegChoreographer(ctk.CTk):
    """马机器人腿部动作编排器"""
    
    def __init__(self):
        super().__init__()
        
        self.title("🐴 马机器人腿部动作编排器 - Horse Leg Choreographer")
        self.geometry("1400x800")
        self.minsize(1200, 700)
        
        # 数据
        self.current_gait: Optional[GaitPattern] = None
        self.duration = 2.0
        self.http_ctrl = HTTPController()  # HTTP控制器（主要）
        self.serial_ctrl = SerialController()  # 串口控制器（备选）
        self.use_http = True  # 默认使用HTTP
        self.playing = False
        self.play_thread = None
        self.current_gait_mode = "walk"  # 当前步态模式
        
        # 转向控制
        self.turn_factor = 0.0  # -1.0(左转) 到 +1.0(右转)，0为直行
        self.turn_strength = 0.4  # 转向强度（步幅变化比例）
        self.key_pressed = {"w": False, "a": False, "d": False, "s": False}
        self.modifier_pressed = {"ctrl": False, "shift": False, "alt": False, "space": False}
        
        # 创建UI
        self._create_menu()
        self._create_toolbar()
        self._create_main_area()
        self._create_statusbar()
        
        # 加载默认步态
        self.load_gait(create_walk_gait())
        
        # 绑定键盘事件
        self.bind("<KeyPress>", self._on_key_press)
        self.bind("<KeyRelease>", self._on_key_release)
        self.focus_set()  # 确保窗口获得焦点
        
    def _create_menu(self):
        """创建顶部菜单栏（使用CTk组件）"""
        self.menu_frame = ctk.CTkFrame(self, height=40, fg_color="#1a1a2e")
        self.menu_frame.pack(fill="x", padx=5, pady=5)
        self.menu_frame.pack_propagate(False)
        
        # 文件操作
        ctk.CTkButton(
            self.menu_frame, text="📁 新建", width=80,
            command=self.new_gait
        ).pack(side="left", padx=2)
        
        ctk.CTkButton(
            self.menu_frame, text="📂 打开", width=80,
            command=self.open_file
        ).pack(side="left", padx=2)
        
        ctk.CTkButton(
            self.menu_frame, text="💾 保存", width=80,
            command=self.save_file
        ).pack(side="left", padx=2)
        
        # 分隔
        ctk.CTkLabel(self.menu_frame, text="|", text_color="#444").pack(side="left", padx=10)
        
        # 预设步态
        ctk.CTkLabel(
            self.menu_frame, text="预设步态:",
            font=("Microsoft YaHei", 12)
        ).pack(side="left", padx=5)
        
        ctk.CTkButton(
            self.menu_frame, text="🐴 马步走路", width=100,
            fg_color="#27ae60", hover_color="#219a52",
            command=lambda: self.load_gait(create_walk_gait())
        ).pack(side="left", padx=2)
        
        ctk.CTkButton(
            self.menu_frame, text="🏃 快步", width=80,
            fg_color="#f39c12", hover_color="#d68910",
            command=lambda: self.load_gait(create_trot_gait())
        ).pack(side="left", padx=2)
        
        ctk.CTkButton(
            self.menu_frame, text="🏇 跑步", width=80,
            fg_color="#e74c3c", hover_color="#c0392b",
            command=lambda: self.load_gait(create_gallop_gait())
        ).pack(side="left", padx=2)
        
        # 分隔
        ctk.CTkLabel(self.menu_frame, text="|", text_color="#444").pack(side="left", padx=10)
        
        # 时长设置
        ctk.CTkLabel(
            self.menu_frame, text="时长(秒):",
            font=("Microsoft YaHei", 12)
        ).pack(side="left", padx=5)
        
        self.duration_entry = ctk.CTkEntry(self.menu_frame, width=60)
        self.duration_entry.pack(side="left", padx=2)
        self.duration_entry.insert(0, "2.0")
        self.duration_entry.bind("<Return>", self._on_duration_change)
        
        ctk.CTkButton(
            self.menu_frame, text="设置", width=50,
            command=self._on_duration_change
        ).pack(side="left", padx=2)
        
    def _create_toolbar(self):
        """创建工具栏"""
        self.toolbar = ctk.CTkFrame(self, height=50, fg_color="#0f3460")
        self.toolbar.pack(fill="x", padx=5, pady=2)
        self.toolbar.pack_propagate(False)
        
        # 播放控制
        self.play_btn = ctk.CTkButton(
            self.toolbar, text="▶ 播放", width=80,
            fg_color="#2ecc71", hover_color="#27ae60",
            command=self.toggle_play
        )
        self.play_btn.pack(side="left", padx=5, pady=8)
        
        ctk.CTkButton(
            self.toolbar, text="⏹ 停止", width=80,
            command=self.stop_play
        ).pack(side="left", padx=2, pady=8)
        
        ctk.CTkButton(
            self.toolbar, text="⏮ 重置", width=80,
            command=self.reset_playhead
        ).pack(side="left", padx=2, pady=8)
        
        # 分隔
        ctk.CTkLabel(self.toolbar, text="|", text_color="#444").pack(side="left", padx=10)
        
        # 循环播放
        self.loop_var = ctk.BooleanVar(value=True)
        self.loop_check = ctk.CTkCheckBox(
            self.toolbar, text="循环播放",
            variable=self.loop_var
        )
        self.loop_check.pack(side="left", padx=10, pady=8)
        
        # 分隔
        ctk.CTkLabel(self.toolbar, text="|", text_color="#444").pack(side="left", padx=10)
        
        # ESP32 WiFi 连接
        ctk.CTkLabel(
            self.toolbar, text="ESP32 IP:",
            font=("Microsoft YaHei", 12)
        ).pack(side="left", padx=5)
        
        self.ip_entry = ctk.CTkEntry(self.toolbar, width=130)
        self.ip_entry.pack(side="left", padx=2, pady=8)
        self.ip_entry.insert(0, "192.168.2.72")  # 默认IP
        
        self.connect_btn = ctk.CTkButton(
            self.toolbar, text="🔗 连接", width=70,
            command=self._toggle_connection
        )
        self.connect_btn.pack(side="left", padx=2, pady=8)
        
        self.scan_btn = ctk.CTkButton(
            self.toolbar, text="🔍 扫描舵机", width=90,
            command=self._scan_servos
        )
        self.scan_btn.pack(side="left", padx=2, pady=8)
        
        # 实时发送开关
        self.realtime_var = ctk.BooleanVar(value=False)
        self.realtime_check = ctk.CTkCheckBox(
            self.toolbar, text="实时发送",
            variable=self.realtime_var
        )
        self.realtime_check.pack(side="left", padx=10, pady=8)
        
        # 步态选择
        ctk.CTkLabel(self.toolbar, text="步态:", font=("Microsoft YaHei", 12)).pack(side="left", padx=(10, 2))
        self.gait_mode_var = ctk.StringVar(value="walk")
        self.gait_mode_menu = ctk.CTkOptionMenu(
            self.toolbar,
            variable=self.gait_mode_var,
            values=["walk", "trot", "run", "idle", "sit", "jump"],
            width=80,
            command=self._on_gait_mode_change
        )
        self.gait_mode_menu.pack(side="left", padx=2, pady=8)
        
        # 转向显示
        ctk.CTkLabel(self.toolbar, text="转向:", font=("Microsoft YaHei", 12)).pack(side="left", padx=(15, 2))
        self.turn_label = ctk.CTkLabel(
            self.toolbar, text="直行", 
            font=("Microsoft YaHei", 12, "bold"),
            text_color="#2ecc71",
            width=60
        )
        self.turn_label.pack(side="left", padx=2, pady=8)
        
        # 转向强度滑杆
        ctk.CTkLabel(self.toolbar, text="强度:", font=("Microsoft YaHei", 11)).pack(side="left", padx=(10, 2))
        self.turn_strength_slider = ctk.CTkSlider(
            self.toolbar, from_=0.1, to=0.8, width=80,
            command=self._on_turn_strength_change
        )
        self.turn_strength_slider.set(0.4)
        self.turn_strength_slider.pack(side="left", padx=2, pady=8)
        
        # 键盘提示
        ctk.CTkLabel(
            self.toolbar, text="WASD:walk +Ctrl:trot +Shift:run +Space:jump Alt:sit", 
            font=("Microsoft YaHei", 9),
            text_color="#7f8c8d"
        ).pack(side="left", padx=(10, 2))
        
        # 测试单个舵机按钮
        ctk.CTkButton(
            self.toolbar, text="🧪 测试", width=60,
            fg_color="#9b59b6", hover_color="#8e44ad",
            command=self._test_current_keyframe
        ).pack(side="left", padx=2, pady=8)
        
    def _create_main_area(self):
        """创建主区域"""
        self.main_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.main_frame.pack(fill="both", expand=True, padx=5, pady=5)
        
        # 左侧：时间线
        self.timeline_frame = ctk.CTkFrame(self.main_frame)
        self.timeline_frame.pack(side="left", fill="both", expand=True, padx=(0, 5))
        
        # 时间线标题
        timeline_header = ctk.CTkFrame(self.timeline_frame, height=40)
        timeline_header.pack(fill="x")
        timeline_header.pack_propagate(False)
        
        ctk.CTkLabel(
            timeline_header, 
            text="⏱️ 时间线",
            font=("Microsoft YaHei", 14, "bold")
        ).pack(side="left", padx=10, pady=5)
        
        ctk.CTkLabel(
            timeline_header, 
            text="双击添加关键帧 | 右键删除 | 滚轮缩放 | 拖拽调整",
            font=("Microsoft YaHei", 10),
            text_color="#888888"
        ).pack(side="left", padx=20, pady=5)
        
        # 时间线画布
        self.timeline = TimelineCanvas(self.timeline_frame, self)
        self.timeline.pack(fill="both", expand=True)
        
        # 右侧：属性面板
        self.right_panel = ctk.CTkFrame(self.main_frame, width=300)
        self.right_panel.pack(side="right", fill="y", padx=(5, 0))
        self.right_panel.pack_propagate(False)
        
        # 属性编辑器
        self.property_editor = PropertyEditor(self.right_panel, self)
        self.property_editor.pack(fill="x", pady=10)
        
        # 分隔线
        ctk.CTkFrame(self.right_panel, height=2, fg_color="#333").pack(fill="x", pady=10)
        
        # 实时测试面板
        self._create_realtime_test_panel()
        
        # 分隔线
        ctk.CTkFrame(self.right_panel, height=2, fg_color="#333").pack(fill="x", pady=10)
        
        # 导出代码面板
        self._create_export_panel()
        
    def _create_realtime_test_panel(self):
        """创建实时测试面板"""
        test_frame = ctk.CTkFrame(self.right_panel, fg_color="#1a1a2e")
        test_frame.pack(fill="x", pady=5, padx=10)
        
        # 标题
        ctk.CTkLabel(
            test_frame, 
            text="🎮 实时舵机测试",
            font=("Microsoft YaHei", 14, "bold")
        ).pack(pady=(10, 5))
        
        # 舵机选择
        servo_select_frame = ctk.CTkFrame(test_frame, fg_color="transparent")
        servo_select_frame.pack(fill="x", padx=10, pady=5)
        
        ctk.CTkLabel(
            servo_select_frame, 
            text="选择舵机:",
            font=("Microsoft YaHei", 11)
        ).pack(anchor="w")
        
        self.test_servo_var = ctk.StringVar(value="1")
        
        # 舵机选择按钮（带颜色标识）- 分两行显示
        servo_colors = ["#FF6B6B", "#4ECDC4", "#FFE66D", "#95E1D3"]
        servo_names = ["1:左前腿", "2:右前腿", "3:左后腿", "4:右后腿"]
        
        # 第一行：前腿
        row1_frame = ctk.CTkFrame(servo_select_frame, fg_color="transparent")
        row1_frame.pack(fill="x", pady=2)
        
        for i in range(2):
            btn = ctk.CTkRadioButton(
                row1_frame,
                text=servo_names[i],
                variable=self.test_servo_var,
                value=str(i + 1),
                fg_color=servo_colors[i],
                hover_color=servo_colors[i],
                font=("Microsoft YaHei", 11)
            )
            btn.pack(side="left", padx=10)
        
        # 第二行：后腿
        row2_frame = ctk.CTkFrame(servo_select_frame, fg_color="transparent")
        row2_frame.pack(fill="x", pady=2)
        
        for i in range(2, 4):
            btn = ctk.CTkRadioButton(
                row2_frame,
                text=servo_names[i],
                variable=self.test_servo_var,
                value=str(i + 1),
                fg_color=servo_colors[i],
                hover_color=servo_colors[i],
                font=("Microsoft YaHei", 11)
            )
            btn.pack(side="left", padx=10)
        
        # 位置滑杆
        pos_frame = ctk.CTkFrame(test_frame, fg_color="transparent")
        pos_frame.pack(fill="x", padx=10, pady=5)
        
        ctk.CTkLabel(
            pos_frame, 
            text="位置:",
            width=50,
            font=("Microsoft YaHei", 11)
        ).pack(side="left")
        
        self.test_pos_slider = ctk.CTkSlider(
            pos_frame, 
            from_=0, 
            to=4095,
            width=150,
            command=self._on_test_slider_change
        )
        self.test_pos_slider.pack(side="left", padx=5)
        self.test_pos_slider.set(2048)
        
        self.test_pos_label = ctk.CTkLabel(
            pos_frame, 
            text="2048",
            width=50,
            font=("Consolas", 11, "bold")
        )
        self.test_pos_label.pack(side="left")
        
        # 快捷位置按钮
        quick_pos_frame = ctk.CTkFrame(test_frame, fg_color="transparent")
        quick_pos_frame.pack(fill="x", padx=10, pady=2)
        
        ctk.CTkLabel(quick_pos_frame, text="", width=50).pack(side="left")
        
        ctk.CTkButton(
            quick_pos_frame, text="0", width=40, height=24,
            fg_color="#444", hover_color="#555",
            command=lambda: self._set_test_position(0)
        ).pack(side="left", padx=2)
        
        ctk.CTkButton(
            quick_pos_frame, text="1024", width=40, height=24,
            fg_color="#444", hover_color="#555",
            command=lambda: self._set_test_position(1024)
        ).pack(side="left", padx=2)
        
        ctk.CTkButton(
            quick_pos_frame, text="2048", width=40, height=24,
            fg_color="#444", hover_color="#555",
            command=lambda: self._set_test_position(2048)
        ).pack(side="left", padx=2)
        
        ctk.CTkButton(
            quick_pos_frame, text="3072", width=40, height=24,
            fg_color="#444", hover_color="#555",
            command=lambda: self._set_test_position(3072)
        ).pack(side="left", padx=2)
        
        ctk.CTkButton(
            quick_pos_frame, text="4095", width=40, height=24,
            fg_color="#444", hover_color="#555",
            command=lambda: self._set_test_position(4095)
        ).pack(side="left", padx=2)
        
        # 速度滑杆
        speed_frame = ctk.CTkFrame(test_frame, fg_color="transparent")
        speed_frame.pack(fill="x", padx=10, pady=5)
        
        ctk.CTkLabel(
            speed_frame, 
            text="速度:",
            width=50,
            font=("Microsoft YaHei", 11)
        ).pack(side="left")
        
        self.test_speed_slider = ctk.CTkSlider(
            speed_frame, 
            from_=0, 
            to=10000,
            width=150,
            command=self._on_test_speed_change
        )
        self.test_speed_slider.pack(side="left", padx=5)
        self.test_speed_slider.set(0)  # 0表示最快速度
        
        self.test_speed_label = ctk.CTkLabel(
            speed_frame, 
            text="0(最快)",
            width=70,
            font=("Consolas", 11)
        )
        self.test_speed_label.pack(side="left")
        
        # 加速度滑杆
        accel_frame = ctk.CTkFrame(test_frame, fg_color="transparent")
        accel_frame.pack(fill="x", padx=10, pady=5)
        
        ctk.CTkLabel(
            accel_frame, 
            text="加速度:",
            width=50,
            font=("Microsoft YaHei", 11)
        ).pack(side="left")
        
        self.test_accel_slider = ctk.CTkSlider(
            accel_frame, 
            from_=0, 
            to=254,
            width=150,
            command=self._on_test_accel_change
        )
        self.test_accel_slider.pack(side="left", padx=5)
        self.test_accel_slider.set(0)  # 0=无加速度限制=最快响应
        
        self.test_accel_label = ctk.CTkLabel(
            accel_frame, 
            text="0(直接)",
            width=70,
            font=("Consolas", 11)
        )
        self.test_accel_label.pack(side="left")
        
        # 实时发送开关
        realtime_frame = ctk.CTkFrame(test_frame, fg_color="transparent")
        realtime_frame.pack(fill="x", padx=10, pady=5)
        
        self.test_realtime_var = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(
            realtime_frame,
            text="拖动时实时发送",
            variable=self.test_realtime_var,
            font=("Microsoft YaHei", 11)
        ).pack(side="left")
        
        # 发送按钮
        btn_frame = ctk.CTkFrame(test_frame, fg_color="transparent")
        btn_frame.pack(fill="x", padx=10, pady=(5, 10))
        
        ctk.CTkButton(
            btn_frame, 
            text="📤 发送命令",
            fg_color="#27ae60",
            hover_color="#219a52",
            command=self._send_test_command
        ).pack(side="left", padx=2, expand=True, fill="x")
        
        ctk.CTkButton(
            btn_frame, 
            text="🏠 回中位",
            fg_color="#3498db",
            hover_color="#2980b9",
            command=self._reset_to_center
        ).pack(side="left", padx=2, expand=True, fill="x")
        
        # 全部回中
        ctk.CTkButton(
            test_frame, 
            text="🔄 全部舵机回中位",
            fg_color="#9b59b6",
            hover_color="#8e44ad",
            command=self._reset_all_to_center
        ).pack(fill="x", padx=10, pady=(0, 10))
        
    def _on_test_slider_change(self, value):
        """测试位置滑杆变化"""
        pos = int(value)
        self.test_pos_label.configure(text=str(pos))
        
        # 实时发送
        if self.test_realtime_var.get() and self.http_ctrl.connected:
            self._send_test_command()
            
    def _on_gait_mode_change(self, value):
        """步态模式切换"""
        if value == "trot":
            self.update_status("切换到快走(Trot)模式")
            print("[GAIT] 切换到快走(Trot)模式")
        elif value == "run":
            self.update_status("切换到跑步(Run)模式")
            print("[GAIT] 切换到跑步(Run)模式")
        elif value == "idle":
            self.update_status("切换到静止待机(Idle)模式")
            print("[GAIT] 切换到静止待机(Idle)模式")
        elif value == "sit":
            self.update_status("切换到坐下(Sit)模式")
            print("[GAIT] 切换到坐下(Sit)模式")
        elif value == "jump":
            self.update_status("切换到跳跃(Jump)模式")
            print("[GAIT] 切换到跳跃(Jump)模式")
        else:
            self.update_status("切换到慢走(Walk)模式")
            print("[GAIT] 切换到慢走(Walk)模式")
    
    def _on_turn_strength_change(self, value):
        """转向强度变化"""
        self.turn_strength = value
    
    def _on_key_press(self, event):
        """键盘按下事件"""
        key = event.keysym.lower()
        # print(f"[DEBUG] Key press: {key}")  # 调试用
        
        # 检测修饰键
        if key in ["control_l", "control_r", "ctrl"]:
            self.modifier_pressed["ctrl"] = True
            self._check_gait_switch()
            return
        elif key in ["shift_l", "shift_r", "shift"]:
            self.modifier_pressed["shift"] = True
            self._check_gait_switch()
            return
        elif key in ["alt_l", "alt_r", "alt", "menu"]:
            self.modifier_pressed["alt"] = True
            print("[KEY] Alt pressed - triggering sit")
            self._check_gait_switch()
            return
        elif key == "space":
            self.modifier_pressed["space"] = True
            print("[KEY] Space pressed")
            self._check_gait_switch()
            return
        
        # WASD键
        if key == "w":
            self.key_pressed["w"] = True
            self._check_gait_switch()
        elif key == "a":
            self.key_pressed["a"] = True
            self.turn_factor = -1.0  # 左转
            self._update_turn_display()
            self._check_gait_switch()
        elif key == "d":
            self.key_pressed["d"] = True
            self.turn_factor = 1.0  # 右转
            self._update_turn_display()
            self._check_gait_switch()
        elif key == "s":
            self.key_pressed["s"] = True
            # S键：停止播放
            if self.playing:
                self.stop_play()
    
    def _on_key_release(self, event):
        """键盘释放事件"""
        key = event.keysym.lower()
        
        # 检测修饰键释放
        if key in ["control_l", "control_r", "ctrl"]:
            self.modifier_pressed["ctrl"] = False
            self._check_gait_switch()
            return
        elif key in ["shift_l", "shift_r", "shift"]:
            self.modifier_pressed["shift"] = False
            self._check_gait_switch()
            return
        elif key in ["alt_l", "alt_r", "alt", "menu"]:
            self.modifier_pressed["alt"] = False
            self._check_gait_switch()
            return
        elif key == "space":
            self.modifier_pressed["space"] = False
            self._check_gait_switch()
            return
        
        # WASD键释放
        if key == "w":
            self.key_pressed["w"] = False
            self._check_gait_switch()
        elif key == "a":
            self.key_pressed["a"] = False
            if not self.key_pressed["d"]:
                self.turn_factor = 0.0  # 恢复直行
            self._update_turn_display()
            self._check_gait_switch()
        elif key == "d":
            self.key_pressed["d"] = False
            if not self.key_pressed["a"]:
                self.turn_factor = 0.0  # 恢复直行
            self._update_turn_display()
            self._check_gait_switch()
        elif key == "s":
            self.key_pressed["s"] = False
    
    def _check_gait_switch(self):
        """根据按键组合自动切换步态模式"""
        has_wasd = any([self.key_pressed["w"], self.key_pressed["a"], self.key_pressed["d"]])
        
        # 确定目标步态
        if self.modifier_pressed["alt"]:
            # Alt = sit（坐下，不需要WASD）
            target_gait = "sit"
            should_play = True
        elif self.modifier_pressed["space"]:
            # 空格 = jump（跳跃，可以配合WASD转向）
            target_gait = "jump"
            should_play = True
        elif has_wasd:
            if self.modifier_pressed["shift"]:
                # WASD + Shift = run（跑步）
                target_gait = "run"
            elif self.modifier_pressed["ctrl"]:
                # WASD + Ctrl = trot（快走）
                target_gait = "trot"
            else:
                # 只有WASD = walk（慢走）
                target_gait = "walk"
            should_play = True
        else:
            # 无按键 = idle（待机）
            target_gait = "idle"
            should_play = False
        
        # 检查是否需要切换
        current_gait = self.gait_mode_var.get()
        
        if target_gait != current_gait:
            print(f"[KEY] 切换步态: {current_gait} -> {target_gait}")
            
            # 如果正在播放，先停止
            if self.playing:
                self.stop_play()
                # 等待停止完成
                time.sleep(0.1)
            
            # 切换步态
            self.gait_mode_var.set(target_gait)
            self._on_gait_mode_change(target_gait)
            
            # 如果需要播放，自动开始
            if should_play:
                self.after(50, self.toggle_play)  # 快速开始
    
    def _update_turn_display(self):
        """更新转向显示"""
        if self.turn_factor < -0.1:
            self.turn_label.configure(text="← 左转", text_color="#3498db")
        elif self.turn_factor > 0.1:
            self.turn_label.configure(text="右转 →", text_color="#e74c3c")
        else:
            self.turn_label.configure(text="直行", text_color="#2ecc71")
    
    def _on_test_speed_change(self, value):
        """测试速度滑杆变化"""
        speed = int(value)
        if speed == 0:
            self.test_speed_label.configure(text="0(最快)")
        else:
            self.test_speed_label.configure(text=f"{speed}ms")
        
    def _on_test_accel_change(self, value):
        """测试加速度滑杆变化"""
        accel = int(value)
        if accel == 0:
            self.test_accel_label.configure(text="0(直接)")  # 无加速度=最快
        elif accel >= 250:
            self.test_accel_label.configure(text=f"{accel}(平滑)")  # 有加速度曲线
        else:
            self.test_accel_label.configure(text=str(accel))
        
    def _set_test_position(self, pos):
        """设置测试位置"""
        self.test_pos_slider.set(pos)
        self.test_pos_label.configure(text=str(pos))
        
        if self.test_realtime_var.get() and self.http_ctrl.connected:
            self._send_test_command()
            
    def _send_test_command(self):
        """发送测试命令"""
        if not self.http_ctrl.connected:
            self.update_status("请先连接ESP32")
            return
            
        servo_id = int(self.test_servo_var.get())
        position = int(self.test_pos_slider.get())
        speed = int(self.test_speed_slider.get())
        accel = int(self.test_accel_slider.get())
        
        success = self.http_ctrl.send_servo_command(servo_id, position, speed, accel)
        
        if success:
            self.update_status(f"ID{servo_id}: 位置{position} 速度{speed}ms 加速度{accel}")
        else:
            self.update_status("发送失败")
            
    def _reset_to_center(self):
        """当前舵机回中位"""
        self._set_test_position(2048)
        
    def _reset_all_to_center(self):
        """全部舵机回中位"""
        if not self.http_ctrl.connected:
            self.update_status("请先连接ESP32")
            return
            
        speed = int(self.test_speed_slider.get())
        accel = int(self.test_accel_slider.get())
        
        for servo_id in range(1, 5):
            self.http_ctrl.send_servo_command(servo_id, 2048, speed, accel)
            
        self._set_test_position(2048)
        self.update_status("全部舵机已回中位")
        
    def _create_export_panel(self):
        """创建导出代码面板"""
        export_frame = ctk.CTkFrame(self.right_panel)
        export_frame.pack(fill="x", pady=10, padx=10)
        
        ctk.CTkLabel(
            export_frame, 
            text="📤 导出代码",
            font=("Microsoft YaHei", 14, "bold")
        ).pack(pady=10)
        
        ctk.CTkButton(
            export_frame, text="导出 Arduino 代码",
            command=self.export_arduino_code
        ).pack(fill="x", padx=10, pady=5)
        
        ctk.CTkButton(
            export_frame, text="导出 JSON 数据",
            command=self.export_json
        ).pack(fill="x", padx=10, pady=5)
        
        ctk.CTkButton(
            export_frame, text="复制到剪贴板",
            command=self.copy_to_clipboard
        ).pack(fill="x", padx=10, pady=5)
        
    def _create_statusbar(self):
        """创建状态栏"""
        self.statusbar = ctk.CTkFrame(self, height=30, fg_color="#0f3460")
        self.statusbar.pack(fill="x", padx=5, pady=5)
        self.statusbar.pack_propagate(False)
        
        self.status_label = ctk.CTkLabel(
            self.statusbar, 
            text="就绪 | 步态: 马步走路 | 时长: 2.0秒",
            font=("Microsoft YaHei", 10)
        )
        self.status_label.pack(side="left", padx=10, pady=5)
        
        self.serial_status = ctk.CTkLabel(
            self.statusbar, 
            text="● 未连接ESP32",
            font=("Microsoft YaHei", 10),
            text_color="#e74c3c"
        )
        self.serial_status.pack(side="right", padx=10, pady=5)
        
    def load_gait(self, gait: GaitPattern):
        """加载步态模式"""
        self.current_gait = gait
        self.duration = gait.duration
        
        self.duration_entry.delete(0, "end")
        self.duration_entry.insert(0, str(gait.duration))
        
        self.timeline.set_tracks(gait.tracks, gait.duration)
        self.update_status(f"已加载步态: {gait.name}")
        
    def new_gait(self):
        """新建步态"""
        # 创建空白轨道
        tracks = []
        for config in SERVO_CONFIG:
            track = ServoTrack(
                servo_id=config["id"],
                name=config["name"],
                color=config["color"],
                keyframes=[
                    Keyframe(time=0.0, position=POSITION_CENTER, speed=500, acceleration=50),
                    Keyframe(time=self.duration, position=POSITION_CENTER, speed=500, acceleration=50),
                ]
            )
            tracks.append(track)
            
        gait = GaitPattern(
            name="新建步态",
            description="自定义步态",
            duration=self.duration,
            tracks=tracks
        )
        self.load_gait(gait)
        
    def _on_duration_change(self, event=None):
        """时长改变"""
        try:
            new_duration = float(self.duration_entry.get())
            if new_duration > 0:
                self.duration = new_duration
                self.timeline.duration = new_duration
                self.timeline.redraw()
                self.update_status(f"时长已更新为 {new_duration} 秒")
        except ValueError:
            pass
            
    def on_keyframe_selected(self, track_idx, kf_idx):
        """关键帧被选中"""
        self.property_editor.set_keyframe(track_idx, kf_idx)
        
    def toggle_play(self):
        """切换播放状态"""
        if self.playing:
            self.stop_play()
        else:
            self.start_play()
            
    def start_play(self):
        """开始播放"""
        self.playing = True
        self.play_btn.configure(text="⏸ 暂停", fg_color="#f39c12")
        
        self.play_thread = threading.Thread(target=self._play_loop, daemon=True)
        self.play_thread.start()
        
    def stop_play(self):
        """停止播放"""
        self.playing = False
        self.play_btn.configure(text="▶ 播放", fg_color="#2ecc71")
        
    def reset_playhead(self):
        """重置播放头"""
        self.timeline.playhead_time = 0
        self.timeline.redraw()
        
    def _play_loop(self):
        """播放循环 - 支持walk/trot/run/idle四种步态
        
        分三个阶段：
        1. 进入阶段：从2048移动到循环起始位置（带相位差）
        2. 循环阶段：持续循环（每个舵机按自己的相位执行周期）
        3. 退出阶段：每个舵机完成当前半周期后回到2048
        """
        # 根据步态模式选择配置
        gait_mode = self.gait_mode_var.get()
        
        # 特殊动作模式
        if gait_mode == "idle":
            self.current_gait_mode = "idle"
            print("[GAIT] 使用静止待机(Idle)步态")
            self._idle_loop()
            return
        
        if gait_mode == "sit":
            self.current_gait_mode = "sit"
            print("[GAIT] 执行坐下(Sit)动作")
            self._sit_action()
            return
        
        if gait_mode == "jump":
            self.current_gait_mode = "jump"
            print("[GAIT] 执行跳跃(Jump)动作")
            self._jump_action()
            return
        
        if gait_mode == "trot":
            gait_config = TROT_GAIT_CONFIG
            phase_delay = TROT_PHASE_DELAY
            self.current_gait_mode = "trot"
            print("[GAIT] 使用快走(Trot)步态")
        elif gait_mode == "run":
            gait_config = RUN_GAIT_CONFIG
            phase_delay = RUN_PHASE_DELAY
            self.current_gait_mode = "run"
            print("[GAIT] 使用跑步(Run)步态")
        else:
            gait_config = WALK_GAIT_CONFIG
            phase_delay = WALK_PHASE_DELAY
            self.current_gait_mode = "walk"
            print("[GAIT] 使用慢走(Walk)步态")
        
        # 进入阶段：按相位差依次启动
        if self.realtime_var.get() and self.http_ctrl.connected:
            self._enter_gait(gait_config, phase_delay)
        
        # 记录每个舵机下一次发送命令的时间
        # next_time: 下一次发送命令的绝对时间
        # next_phase: 下一次要发送的阶段 (0=前半程, 1=后半程)
        now = time.time()
        servo_state = {}
        for sid, cfg in gait_config.items():
            phase_start = now + cfg["phase"] * phase_delay
            servo_state[sid] = {
                "next_time": phase_start,  # 第一次命令时间
                "next_phase": 0,  # 从前半程开始
            }
        
        start_time = time.time()
        
        while self.playing:
            elapsed = time.time() - start_time
            
            # 更新播放头（用于UI显示）
            display_time = elapsed % self.duration
            self.timeline.playhead_time = display_time
            
            # 如果启用实时发送，执行循环
            if self.realtime_var.get() and self.http_ctrl.connected:
                self._run_gait_cycle_precise(gait_config, servo_state, gait_mode)
                
            # 在主线程中更新UI
            self.after(0, self.timeline.redraw)
            
            time.sleep(0.01)  # 10ms检查间隔（更精确）
            
        # 退出阶段：每个舵机完成当前半周期后依次回到2048
        if self.realtime_var.get() and self.http_ctrl.connected:
            self._exit_gait(gait_config, servo_state, phase_delay)
            
        self.after(0, lambda: self.play_btn.configure(text="▶ 播放", fg_color="#2ecc71"))
        
    def _enter_gait(self, gait_config, phase_delay):
        """进入步态：从统一起始位(2048)有序移动到循环起始位置(ready)（带相位差）
        
        算法：
        1. 先确保所有舵机在2048（统一起始位）
        2. 按相位顺序，每个舵机依次从2048移动到ready位置
        3. 每个舵机间隔phase_delay秒开始移动
        4. 移动使用过渡加速度，平滑进入
        """
        self.update_status("进入步态...")
        
        # 按相位顺序排序
        sorted_servos = sorted(gait_config.items(), key=lambda x: x[1]["phase"])
        
        # 先让所有舵机回到2048（统一起始位）
        STARTING_POS = 2048
        for servo_id, cfg in sorted_servos:
            print(f"[INIT] 舵机{servo_id}({cfg['name']}) -> {STARTING_POS}")
            self.http_ctrl.send_servo_command(servo_id, STARTING_POS, 1500, 200, True)  # 更快速度
        time.sleep(0.3)  # 快速等待
        
        # 根据步态模式选择参数
        if self.current_gait_mode == "trot":
            phase_4_time = TROT_PHASE_4
            accel_trans = TROT_ACCEL_TRANS
        elif self.current_gait_mode == "run":
            phase_4_time = RUN_PHASE_4
            accel_trans = RUN_ACCEL_TRANS
        else:
            phase_4_time = WALK_PHASE_4
            accel_trans = WALK_ACCEL_TRANS
        
        # 依次按相位差进入ready位置
        for servo_id, cfg in sorted_servos:
            # 计算从2048到ready的位移和速度
            displacement = abs(cfg["ready"] - STARTING_POS)
            # 用较慢的速度进入
            speed = calc_speed(displacement, phase_4_time * 0.8)  # 稍快一点
            
            # 发送到准备位置
            threading.Thread(
                target=self.http_ctrl.send_servo_command,
                args=(servo_id, cfg["ready"], speed, accel_trans, True),
                daemon=True
            ).start()
            
            # 等待相位差时间后下一个舵机开始
            time.sleep(phase_delay)
            
    def _run_gait_cycle_precise(self, gait_config, servo_state, gait_mode="walk"):
        """精确时间触发的步态循环（四阶段运动）
        
        阶段0: ready → back - 向后蓄力
        阶段1: back → front - 发力向前推
        阶段2: front → neutral - 回到中立
        阶段3: neutral → ready - 收回准备位
        
        速度根据每个舵机的实际位移量计算，确保刚好在规定时间内完成
        前后半程交接使用过渡加速度平滑速度变化
        
        转向控制：通过调整左右两侧腿的步幅实现
        - 左转：右侧腿步幅增大，左侧腿步幅减小
        - 右转：左侧腿步幅增大，右侧腿步幅减小
        """
        # 根据步态模式选择参数
        if gait_mode == "trot":
            phase_1 = TROT_PHASE_1
            phase_2 = TROT_PHASE_2
            phase_3 = TROT_PHASE_3
            phase_4 = TROT_PHASE_4
            accel_fast = TROT_ACCEL_FAST
            accel_trans = TROT_ACCEL_TRANS
        elif gait_mode == "run":
            phase_1 = RUN_PHASE_1
            phase_2 = RUN_PHASE_2
            phase_3 = RUN_PHASE_3
            phase_4 = RUN_PHASE_4
            accel_fast = RUN_ACCEL_FAST
            accel_trans = RUN_ACCEL_TRANS
        else:
            phase_1 = WALK_PHASE_1
            phase_2 = WALK_PHASE_2
            phase_3 = WALK_PHASE_3
            phase_4 = WALK_PHASE_4
            accel_fast = WALK_ACCEL_FAST
            accel_trans = WALK_ACCEL_TRANS
        
        now = time.time()
        
        for servo_id, cfg in gait_config.items():
            state = servo_state[servo_id]
            
            # 计算转向步幅调整系数
            # 左侧腿(1,3): turn_factor > 0 时增大步幅，< 0 时减小
            # 右侧腿(2,4): turn_factor < 0 时增大步幅，> 0 时减小
            is_left_leg = servo_id in [1, 3]
            if is_left_leg:
                # 左侧腿：右转时步幅增大
                stride_scale = 1.0 + self.turn_factor * self.turn_strength
            else:
                # 右侧腿：左转时步幅增大
                stride_scale = 1.0 - self.turn_factor * self.turn_strength
            
            # 限制步幅缩放范围
            stride_scale = max(0.3, min(1.5, stride_scale))
            
            # 根据步幅缩放计算调整后的位置
            neutral = cfg["neutral"]
            back_offset = cfg["back"] - neutral
            front_offset = cfg["front"] - neutral
            ready_offset = cfg["ready"] - neutral
            
            adjusted_back = int(neutral + back_offset * stride_scale)
            adjusted_front = int(neutral + front_offset * stride_scale)
            adjusted_ready = int(neutral + ready_offset * stride_scale)
            
            # 检查是否到了发送下一个命令的时间
            if now >= state["next_time"]:
                phase = state["next_phase"]
                
                if phase == 0:
                    # 阶段1：ready → back，向后蓄力
                    pos = adjusted_back
                    displacement = abs(adjusted_back - adjusted_ready)
                    speed = calc_speed(displacement, phase_1)
                    accel = accel_fast
                    state["next_time"] = now + phase_1
                    state["next_phase"] = 1
                elif phase == 1:
                    # 阶段2：back → front，发力向前推
                    pos = adjusted_front
                    displacement = abs(adjusted_front - adjusted_back)
                    speed = calc_speed(displacement, phase_2)
                    accel = accel_fast
                    state["next_time"] = now + phase_2
                    state["next_phase"] = 2
                elif phase == 2:
                    # 阶段3：front → neutral，回到中立（开始减速，过渡到后半程）
                    pos = neutral  # neutral不需要调整
                    displacement = abs(neutral - adjusted_front)
                    speed = calc_speed(displacement, phase_3)
                    accel = accel_trans  # 过渡加速度，平滑减速
                    state["next_time"] = now + phase_3
                    state["next_phase"] = 3
                else:
                    # 阶段4：neutral → ready，收回准备位
                    pos = adjusted_ready
                    displacement = abs(adjusted_ready - neutral)
                    speed = calc_speed(displacement, phase_4)
                    accel = accel_trans  # 过渡加速度
                    state["next_time"] = now + phase_4
                    state["next_phase"] = 0
                
                # 异步发送命令
                threading.Thread(
                    target=self.http_ctrl.send_servo_command,
                    args=(servo_id, pos, speed, accel, True),
                    daemon=True
                ).start()
                
    def _exit_gait(self, gait_config, servo_state, phase_delay):
        """退出步态：每个舵机有序完成当前动作后依次回到2048
        
        算法：
        1. 按相位顺序，每个舵机等待完成当前阶段动作
        2. 强制发送到2048（不管当前在哪）
        3. 每个舵机间隔phase_delay秒开始退出
        4. 使用过渡加速度平滑退出
        5. 最后逐个确认每个舵机都到达2048
        """
        self.update_status("停止中...")
        
        # 按相位顺序排序（先进先出，模拟自然停止）
        sorted_servos = sorted(gait_config.items(), key=lambda x: x[1]["phase"])
        
        for servo_id, cfg in sorted_servos:
            state = servo_state[servo_id]
            
            # 计算需要等待的时间，让当前动作完成
            now = time.time()
            wait_time = state["next_time"] - now
            
            if wait_time > 0:
                # 等待当前动作完成
                time.sleep(wait_time + 0.1)
            
            # 强制发送到2048（统一结束位，使用固定速度，确保平滑）
            # 最大可能的位移用于计算安全速度
            ENDING_POS = 2048
            max_displacement = max(
                abs(ENDING_POS - cfg["ready"]),
                abs(ENDING_POS - cfg["back"]),
                abs(ENDING_POS - cfg["front"])
            )
            speed = calc_speed(max_displacement, 0.8)  # 0.8秒内完成
            
            # 发送到2048
            print(f"[EXIT] 舵机{servo_id}({cfg['name']}) -> {ENDING_POS}, 速度{speed}")
            self.http_ctrl.send_servo_command(
                servo_id, ENDING_POS, speed, WALK_ACCEL_TRANS, True
            )
            
            # 等待这个舵机到达中立位后再处理下一个
            time.sleep(0.3)  # 快速等待
        
        # 等待所有动作完成
        time.sleep(0.15)
        
        # 最后让所有舵机回到2048（统一结束位）
        self.update_status("回到2048...")
        ENDING_POS = 2048
        for servo_id, cfg in sorted_servos:
            print(f"[FINAL] 舵机{servo_id}({cfg['name']}) -> {ENDING_POS}")
            self.http_ctrl.send_servo_command(servo_id, ENDING_POS, 300, 50, True)
            time.sleep(0.4)  # 等待每个舵机到位
        
        time.sleep(0.3)
        self.update_status("已停止")
    
    def _idle_loop(self):
        """静止待机循环 - 模拟马自然站立
        
        特点：
        1. 四个腿在2048附近微微晃动
        2. 晃动有相位差，看起来自然
        3. 偶尔随机抬起一只脚踢几下
        4. 没有固定周期，完全随机
        """
        import random
        
        self.update_status("静止待机中...")
        
        # 先让所有舵机回到2048
        for servo_id, cfg in IDLE_GAIT_CONFIG.items():
            self.http_ctrl.send_servo_command(servo_id, cfg["base"], 1000, 150, True)
        time.sleep(0.3)
        
        # 每个舵机的状态
        servo_state = {}
        now = time.time()
        for servo_id, cfg in IDLE_GAIT_CONFIG.items():
            # 根据相位错开初始时间
            phase_offset = cfg["phase"] * 0.3
            servo_state[servo_id] = {
                "current_pos": cfg["base"],
                "next_sway_time": now + phase_offset + random.uniform(0.5, 1.5),
                "is_kicking": False,
                "kick_count": 0,
                "kick_target": 0,
            }
        
        while self.playing:
            now = time.time()
            
            for servo_id, cfg in IDLE_GAIT_CONFIG.items():
                state = servo_state[servo_id]
                
                # 如果正在踢腿
                if state["is_kicking"]:
                    continue  # 踢腿动作在单独线程中处理
                
                # 检查是否到了晃动时间
                if now >= state["next_sway_time"]:
                    # 决定是踢腿还是微晃
                    if random.random() < IDLE_KICK_CHANCE:
                        # 触发踢腿！
                        state["is_kicking"] = True
                        kick_count = random.randint(IDLE_KICK_COUNT_MIN, IDLE_KICK_COUNT_MAX)
                        threading.Thread(
                            target=self._do_kick,
                            args=(servo_id, cfg, state, kick_count),
                            daemon=True
                        ).start()
                    else:
                        # 微晃
                        self._do_sway(servo_id, cfg, state)
                    
                    # 设置下次晃动时间（随机）
                    state["next_sway_time"] = now + random.uniform(IDLE_SWAY_MIN_TIME, IDLE_SWAY_MAX_TIME)
            
            time.sleep(0.05)  # 50ms检查间隔
        
        # 停止后回到2048
        self.update_status("回到2048...")
        for servo_id, cfg in IDLE_GAIT_CONFIG.items():
            self.http_ctrl.send_servo_command(servo_id, 2048, 800, 150, True)
            time.sleep(0.08)
        time.sleep(0.15)
        self.update_status("已停止")
        self.after(0, lambda: self.play_btn.configure(text="▶ 播放", fg_color="#2ecc71"))
    
    def _do_sway(self, servo_id, cfg, state):
        """执行微晃动作"""
        import random
        
        base = cfg["base"]
        sway_range = cfg["sway_range"]
        
        # 随机目标位置（在基础位置附近）
        offset = random.randint(-sway_range, sway_range)
        target_pos = base + offset
        
        # 随机速度和加速度（慢速，自然感）
        speed = random.randint(80, 200)
        accel = random.randint(20, 60)
        
        # 发送命令
        threading.Thread(
            target=self.http_ctrl.send_servo_command,
            args=(servo_id, target_pos, speed, accel, True),
            daemon=True
        ).start()
        
        state["current_pos"] = target_pos
    
    def _do_kick(self, servo_id, cfg, state, kick_count):
        """执行踢腿动作（在单独线程中运行）
        
        有20%概率触发高踢腿：
        - 前腿(1,2)：向前踢，高点40-70度，低点20-40度
        - 后腿(3,4)：向后踢，高点40-70度，低点20-40度
        """
        import random
        
        base = cfg["base"]
        kick_range = cfg["kick_range"]
        
        # 判断是否是高踢腿（20%概率）
        is_high_kick = random.random() < 0.2
        
        # 判断是前腿还是后腿
        is_front_leg = servo_id in [1, 2]
        
        if is_high_kick:
            print(f"[IDLE] 舵机{servo_id}({cfg['name']}) 开始高踢腿 {kick_count}次")
            
            # 高踢腿参数（角度转换为步数，假设45度≈500步）
            # 高点：40-70度范围，约450-780步
            # 低点：20-40度范围，约220-450步
            HIGH_KICK_MAX = 780  # 70度对应的步数
            HIGH_KICK_MIN = 450  # 40度对应的步数
            LOW_KICK_MAX = 450   # 40度对应的步数
            LOW_KICK_MIN = 220   # 20度对应的步数
            
            for i in range(kick_count):
                if not self.playing:
                    break
                
                # 随机高点和低点幅度
                high_amplitude = random.randint(HIGH_KICK_MIN, HIGH_KICK_MAX)
                low_amplitude = random.randint(LOW_KICK_MIN, LOW_KICK_MAX)
                
                # 确定踢腿方向（前腿向前，后腿向后）
                # 前腿：1号左前腿往小方向踢（减），2号右前腿往大方向踢（加）
                # 后腿：3号左后腿往大方向踢（加），4号右后腿往小方向踢（减）
                if servo_id == 1:  # 左前腿
                    high_pos = base - high_amplitude
                    low_pos = base - low_amplitude
                elif servo_id == 2:  # 右前腿
                    high_pos = base + high_amplitude
                    low_pos = base + low_amplitude
                elif servo_id == 3:  # 左后腿
                    high_pos = base + high_amplitude
                    low_pos = base + low_amplitude
                else:  # 4号右后腿
                    high_pos = base - high_amplitude
                    low_pos = base - low_amplitude
                
                # 快速踢到高点
                kick_speed = random.randint(2000, 3000)
                kick_accel = random.randint(230, 254)
                self.http_ctrl.send_servo_command(servo_id, high_pos, kick_speed, kick_accel, True)
                time.sleep(random.uniform(0.1, 0.18))
                
                # 快速到低点
                mid_speed = random.randint(1800, 2500)
                mid_accel = random.randint(200, 254)
                self.http_ctrl.send_servo_command(servo_id, low_pos, mid_speed, mid_accel, True)
                time.sleep(random.uniform(0.08, 0.15))
                
                # 踢腿间隔（高踢腿间隔稍长）
                time.sleep(random.uniform(0.15, 0.3))
        else:
            print(f"[IDLE] 舵机{servo_id}({cfg['name']}) 开始普通踢腿 {kick_count}次")
            
            for i in range(kick_count):
                if not self.playing:
                    break
                
                # 随机踢腿幅度（每次不同）
                kick_amplitude = random.randint(int(kick_range * 0.5), kick_range)
                
                # 随机方向（正或负）
                direction = random.choice([-1, 1])
                kick_pos = base + direction * kick_amplitude
                
                # 快速踢出
                kick_speed = random.randint(1500, 2500)
                kick_accel = random.randint(200, 254)
                
                self.http_ctrl.send_servo_command(servo_id, kick_pos, kick_speed, kick_accel, True)
                
                # 随机停留时间
                time.sleep(random.uniform(0.08, 0.15))
                
                # 快速收回
                return_speed = random.randint(1200, 2000)
                return_accel = random.randint(180, 254)
                
                self.http_ctrl.send_servo_command(servo_id, base, return_speed, return_accel, True)
                
                # 踢腿间隔
                time.sleep(random.uniform(0.1, 0.25))
        
        # 踢完后回到基础位置
        self.http_ctrl.send_servo_command(servo_id, base, 200, 50, True)
        time.sleep(0.3)
        
        state["is_kicking"] = False
        state["current_pos"] = base
        print(f"[IDLE] 舵机{servo_id}({cfg['name']}) 踢腿结束")
    
    def _sit_action(self):
        """坐下动作 - 后腿向前倒下，前腿随机动作
        
        1. 后腿一只一只向前倒下（70度）
        2. 前腿随机动作：往前动、俯卧撑、交替交叉
        """
        import random
        
        self.update_status("执行坐下动作...")
        
        # 先让所有舵机回到2048
        for sid in [1, 2, 3, 4]:
            self.http_ctrl.send_servo_command(sid, 2048, 1200, 180, True)
        time.sleep(0.25)
        
        # 后腿位置（向前倒70度，约778步）
        # 3号左后腿：向前 = 位置增大
        # 4号右后腿：向前 = 位置减小
        BACK_LEG_ANGLE = 778  # 70度
        left_back_sit = 2048 + BACK_LEG_ANGLE   # 2826
        right_back_sit = 2048 - BACK_LEG_ANGLE  # 1270
        
        # 后腿一只一只倒下
        print("[SIT] 左后腿倒下...")
        self.http_ctrl.send_servo_command(3, left_back_sit, 600, 150, True)
        time.sleep(0.35)
        
        print("[SIT] 右后腿倒下...")
        self.http_ctrl.send_servo_command(4, right_back_sit, 600, 150, True)
        time.sleep(0.35)
        
        self.update_status("坐下完成，前腿随机动作中...")
        
        # 前腿随机动作循环
        front_leg_actions = ["forward", "pushup", "alternate"]
        
        while self.playing:
            action = random.choice(front_leg_actions)
            
            if action == "forward":
                # 往前动一动
                self._sit_front_forward()
            elif action == "pushup":
                # 做俯卧撑
                self._sit_front_pushup()
            else:
                # 交替交叉动
                self._sit_front_alternate()
            
            # 随机间隔
            time.sleep(random.uniform(0.5, 1.5))
        
        # 停止后站起来
        self._stand_up_from_sit()
    
    def _sit_front_forward(self):
        """坐下时前腿往前动一动"""
        import random
        
        # 随机选择一只或两只前腿
        legs = random.choice([[1], [2], [1, 2]])
        
        for leg in legs:
            if not self.playing:
                return
            
            # 向前动的幅度（随机）
            amplitude = random.randint(150, 350)
            # 1号左前腿向前 = 减小，2号右前腿向前 = 增大
            if leg == 1:
                target = 2048 - amplitude
            else:
                target = 2048 + amplitude
            
            speed = random.randint(400, 800)
            self.http_ctrl.send_servo_command(leg, target, speed, 100, True)
        
        time.sleep(random.uniform(0.3, 0.6))
        
        # 回到中立
        for leg in legs:
            self.http_ctrl.send_servo_command(leg, 2048, 400, 80, True)
        time.sleep(0.3)
    
    def _sit_front_pushup(self):
        """坐下时前腿做俯卧撑"""
        import random
        
        pushup_count = random.randint(2, 4)
        
        for i in range(pushup_count):
            if not self.playing:
                return
            
            # 两只前腿同时向下（收缩）
            # 1号左前腿收缩 = 增大，2号右前腿收缩 = 减小
            amplitude = random.randint(200, 400)
            
            self.http_ctrl.send_servo_command(1, 2048 + amplitude, 600, 150, True)
            self.http_ctrl.send_servo_command(2, 2048 - amplitude, 600, 150, True)
            time.sleep(0.25)
            
            # 推起来
            self.http_ctrl.send_servo_command(1, 2048, 500, 120, True)
            self.http_ctrl.send_servo_command(2, 2048, 500, 120, True)
            time.sleep(0.25)
    
    def _sit_front_alternate(self):
        """坐下时前腿交替交叉动"""
        import random
        
        alternate_count = random.randint(3, 6)
        
        for i in range(alternate_count):
            if not self.playing:
                return
            
            amplitude = random.randint(150, 300)
            
            if i % 2 == 0:
                # 左前腿向前，右前腿向后
                self.http_ctrl.send_servo_command(1, 2048 - amplitude, 500, 120, True)
                self.http_ctrl.send_servo_command(2, 2048 - amplitude, 500, 120, True)
            else:
                # 左前腿向后，右前腿向前
                self.http_ctrl.send_servo_command(1, 2048 + amplitude, 500, 120, True)
                self.http_ctrl.send_servo_command(2, 2048 + amplitude, 500, 120, True)
            
            time.sleep(0.2)
        
        # 回到中立
        self.http_ctrl.send_servo_command(1, 2048, 400, 80, True)
        self.http_ctrl.send_servo_command(2, 2048, 400, 80, True)
        time.sleep(0.2)
    
    def _stand_up_from_sit(self):
        """从坐下状态站起来"""
        self.update_status("站起来...")
        
        # 前腿先回到中立
        self.http_ctrl.send_servo_command(1, 2048, 800, 150, True)
        self.http_ctrl.send_servo_command(2, 2048, 800, 150, True)
        time.sleep(0.15)
        
        # 后腿一只一只站起来
        print("[SIT] 右后腿站起...")
        self.http_ctrl.send_servo_command(4, 2048, 700, 150, True)
        time.sleep(0.25)
        
        print("[SIT] 左后腿站起...")
        self.http_ctrl.send_servo_command(3, 2048, 700, 150, True)
        time.sleep(0.25)
        
        self.update_status("已站起")
        self.after(0, lambda: self.play_btn.configure(text="▶ 播放", fg_color="#2ecc71"))
    
    def _jump_action(self):
        """开心跳跃动作 - 四腿收缩后弹出
        
        1. 四个腿都向内慢慢收缩
        2. 最快速度弹回到中立位过去20度
        3. 回到中立位
        """
        import random
        
        self.update_status("执行跳跃动作...")
        
        # 先让所有舵机回到2048
        for sid in [1, 2, 3, 4]:
            self.http_ctrl.send_servo_command(sid, 2048, 1200, 180, True)
        time.sleep(0.2)
        
        # 收缩位置（向内收缩约40度，约444步）
        # 1号左前腿：向内 = 增大
        # 2号右前腿：向内 = 减小
        # 3号左后腿：向内 = 减小
        # 4号右后腿：向内 = 增大
        CONTRACTION = 444  # 40度
        
        # 弹出过去的幅度（20度，约222步）
        OVERSHOOT = 222  # 20度
        
        while self.playing:
            # 随机收缩幅度（30-50度）
            contraction = random.randint(333, 555)
            base_overshoot = random.randint(180, 280)
            
            # 根据转向调整左右两侧弹出幅度
            # 左转(turn_factor<0)：右侧腿弹出幅度更大
            # 右转(turn_factor>0)：左侧腿弹出幅度更大
            turn_adjust = self.turn_factor * self.turn_strength * base_overshoot
            left_overshoot = int(base_overshoot + turn_adjust)   # 右转时增大
            right_overshoot = int(base_overshoot - turn_adjust)  # 左转时增大
            
            # 限制范围
            left_overshoot = max(100, min(400, left_overshoot))
            right_overshoot = max(100, min(400, right_overshoot))
            
            if abs(self.turn_factor) > 0.1:
                print(f"[JUMP] 收缩... (转向:{self.turn_factor:.1f}, 左:{left_overshoot}, 右:{right_overshoot})")
            else:
                print("[JUMP] 收缩...")
            self.update_status("蓄力中...")
            
            # 收缩（所有腿同时，向内收）
            # 1号左前腿：向内 = 减小
            # 2号右前腿：向内 = 增大
            # 3号左后腿：向内 = 增大
            # 4号右后腿：向内 = 减小
            self.http_ctrl.send_servo_command(1, 2048 - contraction, 500, 100, True)
            self.http_ctrl.send_servo_command(2, 2048 + contraction, 500, 100, True)
            self.http_ctrl.send_servo_command(3, 2048 + contraction, 500, 100, True)
            self.http_ctrl.send_servo_command(4, 2048 - contraction, 500, 100, True)
            
            # 等待收缩完成
            time.sleep(0.6)
            
            if not self.playing:
                break
            
            print("[JUMP] 后腿弹出!")
            self.update_status("跳跃!")
            
            # 后腿先弹出（向外弹，带转向）
            # 3号左后腿：向外 = 减小（使用left_overshoot）
            # 4号右后腿：向外 = 增大（使用right_overshoot）
            self.http_ctrl.send_servo_command(3, 2048 - left_overshoot, 0, 254, True)  # 0=最大速度
            self.http_ctrl.send_servo_command(4, 2048 + right_overshoot, 0, 254, True)
            
            # 0.2秒后前腿弹出
            time.sleep(0.2)
            
            if not self.playing:
                break
            
            print("[JUMP] 前腿弹出!")
            # 1号左前腿：向外 = 增大（使用left_overshoot）
            # 2号右前腿：向外 = 减小（使用right_overshoot）
            self.http_ctrl.send_servo_command(1, 2048 + left_overshoot, 0, 254, True)
            self.http_ctrl.send_servo_command(2, 2048 - right_overshoot, 0, 254, True)
            
            time.sleep(0.1)
            
            if not self.playing:
                break
            
            # 回到中立位
            print("[JUMP] 落地")
            self.http_ctrl.send_servo_command(1, 2048, 800, 150, True)
            self.http_ctrl.send_servo_command(2, 2048, 800, 150, True)
            self.http_ctrl.send_servo_command(3, 2048, 800, 150, True)
            self.http_ctrl.send_servo_command(4, 2048, 800, 150, True)
            
            time.sleep(0.4)
            
            self.update_status("开心跳跃中...")
            
            # 跳跃间隔（随机，模拟自然的开心跳跃）
            time.sleep(random.uniform(0.8, 2.0))
        
        # 停止后回到中立位
        self.update_status("回到中立位...")
        for sid in [1, 2, 3, 4]:
            self.http_ctrl.send_servo_command(sid, 2048, 1000, 150, True)
        time.sleep(0.15)
        self.update_status("已停止")
        self.after(0, lambda: self.play_btn.configure(text="▶ 播放", fg_color="#2ecc71"))
        
    def _send_current_positions(self, current_time):
        """发送当前时间点的所有舵机位置（静默模式，不打印错误）"""
        for track in self.timeline.tracks:
            # 找到当前时间点的位置（线性插值）
            position = self._interpolate_position(track, current_time)
            if position is not None:
                # 找到当前时间点的速度和加速度
                kf = self._get_nearest_keyframe(track, current_time)
                if kf:
                    self.http_ctrl.send_servo_command(
                        track.servo_id, 
                        position, 
                        kf.speed, 
                        kf.acceleration,
                        silent=True  # 静默模式
                    )
                    
    def _interpolate_position(self, track: ServoTrack, t: float) -> Optional[int]:
        """线性插值计算位置"""
        if not track.keyframes:
            return None
            
        # 找到前后两个关键帧
        prev_kf = None
        next_kf = None
        
        for kf in track.keyframes:
            if kf.time <= t:
                prev_kf = kf
            if kf.time >= t and next_kf is None:
                next_kf = kf
                
        if prev_kf is None:
            return track.keyframes[0].position
        if next_kf is None:
            return track.keyframes[-1].position
        if prev_kf == next_kf:
            return prev_kf.position
            
        # 线性插值
        ratio = (t - prev_kf.time) / (next_kf.time - prev_kf.time)
        position = prev_kf.position + ratio * (next_kf.position - prev_kf.position)
        return int(position)
        
    def _get_nearest_keyframe(self, track: ServoTrack, t: float) -> Optional[Keyframe]:
        """获取最近的关键帧"""
        if not track.keyframes:
            return None
            
        nearest = track.keyframes[0]
        min_dist = abs(t - nearest.time)
        
        for kf in track.keyframes:
            dist = abs(t - kf.time)
            if dist < min_dist:
                min_dist = dist
                nearest = kf
                
        return nearest
        
    def _toggle_connection(self):
        """切换WiFi连接"""
        if self.http_ctrl.connected:
            self.http_ctrl.disconnect()
            self.connect_btn.configure(text="🔗 连接")
            self.serial_status.configure(text="● 未连接", text_color="#e74c3c")
        else:
            ip = self.ip_entry.get().strip()
            if not ip:
                messagebox.showerror("错误", "请输入ESP32 IP地址")
                return
                
            self.http_ctrl.set_ip(ip)
            self.update_status("正在连接...")
            
            # 在后台线程测试连接
            def try_connect():
                if self.http_ctrl.connect():
                    self.after(0, lambda: self._on_connected(ip))
                else:
                    self.after(0, lambda: self._on_connect_failed())
                    
            threading.Thread(target=try_connect, daemon=True).start()
            
    def _on_connected(self, ip):
        """连接成功回调"""
        self.connect_btn.configure(text="🔌 断开")
        self.serial_status.configure(text=f"● 已连接 {ip}", text_color="#2ecc71")
        self.update_status(f"已连接到 {ip}")
        
    def _on_connect_failed(self):
        """连接失败回调"""
        self.serial_status.configure(text="● 连接失败", text_color="#e74c3c")
        messagebox.showerror("错误", f"无法连接到 {self.ip_entry.get()}\n请检查:\n1. ESP32是否已开机\n2. IP地址是否正确\n3. 是否在同一网络")
        
    def _scan_servos(self):
        """扫描舵机"""
        if not self.http_ctrl.connected:
            messagebox.showwarning("提示", "请先连接ESP32")
            return
            
        self.update_status("正在扫描舵机...")
        
        def do_scan():
            result = self.http_ctrl.scan_servos()
            self.after(0, lambda: self._on_scan_result(result))
            
        threading.Thread(target=do_scan, daemon=True).start()
        
    def _on_scan_result(self, result):
        """扫描结果回调"""
        self.update_status(f"扫描完成: {result}")
        messagebox.showinfo("扫描结果", result)
        
    def _test_current_keyframe(self):
        """测试当前选中的关键帧"""
        if not self.http_ctrl.connected:
            messagebox.showwarning("提示", "请先连接ESP32")
            return
            
        if self.timeline.selected_keyframe is None:
            messagebox.showwarning("提示", "请先选择一个关键帧")
            return
            
        track_idx, kf_idx = self.timeline.selected_keyframe
        track = self.timeline.tracks[track_idx]
        kf = track.keyframes[kf_idx]
        
        # 发送命令
        success = self.http_ctrl.send_servo_command(
            track.servo_id, 
            kf.position, 
            kf.speed, 
            kf.acceleration
        )
        
        if success:
            self.update_status(f"已发送: ID{track.servo_id} 位置{kf.position} 速度{kf.speed} 加速度{kf.acceleration}")
        else:
            self.update_status("发送失败")
                
    def save_file(self):
        """保存文件"""
        file_path = filedialog.asksaveasfilename(
            defaultextension=".json",
            filetypes=[("JSON文件", "*.json"), ("所有文件", "*.*")],
            initialfile=f"{self.current_gait.name if self.current_gait else 'gait'}.json"
        )
        
        if file_path:
            gait = GaitPattern(
                name=self.current_gait.name if self.current_gait else "自定义步态",
                description="",
                duration=self.duration,
                tracks=self.timeline.tracks
            )
            
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(gait.to_dict(), f, ensure_ascii=False, indent=2)
                
            self.update_status(f"已保存: {file_path}")
            
    def open_file(self):
        """打开文件"""
        file_path = filedialog.askopenfilename(
            filetypes=[("JSON文件", "*.json"), ("所有文件", "*.*")]
        )
        
        if file_path:
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    
                gait = GaitPattern.from_dict(data)
                self.load_gait(gait)
            except Exception as e:
                messagebox.showerror("错误", f"加载失败: {e}")
                
    def export_arduino_code(self):
        """导出Arduino代码"""
        code = self._generate_arduino_code()
        
        file_path = filedialog.asksaveasfilename(
            defaultextension=".h",
            filetypes=[("C头文件", "*.h"), ("所有文件", "*.*")],
            initialfile=f"gait_{self.current_gait.name if self.current_gait else 'custom'}.h"
        )
        
        if file_path:
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(code)
            self.update_status(f"已导出: {file_path}")
            
    def _generate_arduino_code(self) -> str:
        """生成Arduino代码"""
        gait_name = self.current_gait.name if self.current_gait else "custom"
        gait_name_upper = gait_name.replace(" ", "_").upper()
        
        code = f"""// 自动生成的步态代码 - {gait_name}
// 生成时间: {time.strftime('%Y-%m-%d %H:%M:%S')}
// 时长: {self.duration}秒

#ifndef GAIT_{gait_name_upper}_H
#define GAIT_{gait_name_upper}_H

#include <SCServo.h>

// 关键帧结构
struct GaitKeyframe {{
    float time;        // 时间点（秒）
    int position;      // 位置
    int speed;         // 速度（ms）
    int acceleration;  // 加速度
}};

// 轨道结构
struct GaitTrack {{
    int servo_id;
    int keyframe_count;
    const GaitKeyframe* keyframes;
}};

"""
        
        # 生成每个轨道的关键帧数据
        for track in self.timeline.tracks:
            track_name = f"gait_{gait_name_upper}_track{track.servo_id}"
            code += f"// 舵机 {track.servo_id}: {track.name}\n"
            code += f"const GaitKeyframe {track_name}[] = {{\n"
            
            for kf in track.keyframes:
                code += f"    {{{kf.time:.2f}f, {kf.position}, {kf.speed}, {kf.acceleration}}},\n"
                
            code += "};\n\n"
            
        # 生成轨道数组
        code += f"// 步态轨道定义\n"
        code += f"const GaitTrack gait_{gait_name_upper}[] = {{\n"
        
        for track in self.timeline.tracks:
            track_name = f"gait_{gait_name_upper}_track{track.servo_id}"
            count = len(track.keyframes)
            code += f"    {{{track.servo_id}, {count}, {track_name}}},\n"
            
        code += "};\n\n"
        
        # 生成播放函数
        code += f"""// 步态时长
const float GAIT_{gait_name_upper}_DURATION = {self.duration}f;
const int GAIT_{gait_name_upper}_TRACK_COUNT = {len(self.timeline.tracks)};

// 播放步态函数
void playGait_{gait_name_upper}(SMS_STS& sts, float currentTime, bool loop = true) {{
    if (loop) {{
        currentTime = fmod(currentTime, GAIT_{gait_name_upper}_DURATION);
    }}
    
    for (int t = 0; t < GAIT_{gait_name_upper}_TRACK_COUNT; t++) {{
        const GaitTrack& track = gait_{gait_name_upper}[t];
        
        // 找到当前时间点的位置（线性插值）
        int position = track.keyframes[0].position;
        int speed = track.keyframes[0].speed;
        int accel = track.keyframes[0].acceleration;
        
        for (int i = 0; i < track.keyframe_count - 1; i++) {{
            const GaitKeyframe& kf1 = track.keyframes[i];
            const GaitKeyframe& kf2 = track.keyframes[i + 1];
            
            if (currentTime >= kf1.time && currentTime <= kf2.time) {{
                float ratio = (currentTime - kf1.time) / (kf2.time - kf1.time);
                position = kf1.position + ratio * (kf2.position - kf1.position);
                speed = kf1.speed;
                accel = kf1.acceleration;
                break;
            }}
        }}
        
        sts.WritePosEx(track.servo_id, position, speed, accel);
    }}
}}

#endif // GAIT_{gait_name_upper}_H
"""
        
        return code
        
    def export_json(self):
        """导出JSON数据"""
        self.save_file()
        
    def copy_to_clipboard(self):
        """复制代码到剪贴板"""
        code = self._generate_arduino_code()
        self.clipboard_clear()
        self.clipboard_append(code)
        self.update_status("代码已复制到剪贴板")
        
    def update_status(self, message):
        """更新状态栏"""
        gait_name = self.current_gait.name if self.current_gait else "未加载"
        self.status_label.configure(text=f"{message} | 步态: {gait_name} | 时长: {self.duration}秒")

# ===== 入口 =====
if __name__ == "__main__":
    app = LegChoreographer()
    app.mainloop()

