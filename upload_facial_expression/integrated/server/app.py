# app.py - 集成后端服务
# 功能：WebSocket视频/音频传输 + ASR语音识别 + AI对话 + 硬件控制
# -*- coding: utf-8 -*-

import os
import sys
import time
import json
import asyncio
import base64
import audioop
import io
import threading
from typing import Any, Dict, Optional, List, Set, Deque, Tuple
from collections import deque

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.responses import HTMLResponse, PlainTextResponse
from starlette.websockets import WebSocketState
import uvicorn

# Windows 事件循环策略
if sys.platform.startswith("win"):
    try:
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    except Exception:
        pass

# 环境变量
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

# DashScope ASR
try:
    from dashscope import audio as dash_audio
    ASR_AVAILABLE = True
except ImportError:
    ASR_AVAILABLE = False
    print("[WARNING] dashscope not installed, ASR disabled")

try:
    import cv2  # type: ignore
    HOST_CAMERA_AVAILABLE = True
except Exception:
    cv2 = None
    HOST_CAMERA_AVAILABLE = False
    print("[WARNING] opencv-python not installed, host camera screen disabled")

try:
    from PIL import Image, ImageDraw, ImageFont, ImageStat  # type: ignore
    PIL_AVAILABLE = True
except Exception:
    Image = ImageDraw = ImageFont = ImageStat = None
    PIL_AVAILABLE = False
    print("[WARNING] Pillow not installed, advanced screen rendering disabled")

# 引入我们的模块
from audio_stream import (
    register_stream_route,
    broadcast_pcm16_realtime,
    hard_reset_audio,
    BYTES_PER_20MS_16K,
    is_playing_now,
    stream_clients,
    STREAM_SR,
)
from omni_client import stream_chat, OmniStreamPiece
from asr_core import (
    ASRCallback,
    set_current_recognition,
    stop_current_recognition,
)
from emotion_parser import analyze_emotion
from emotion_action import generate_emotion_actions, format_emact_command, send_emotion_actions
from voice_command import match_voice_command, match_voice_action
from openclaw_client import is_enabled as openclaw_is_enabled, send_prompt_fire_and_forget as openclaw_send_prompt

# 情绪 → 表情动画编号映射
# anim1=idle1, anim2=idle2, anim3=idle3, anim4=cry, anim5=angry, anim6=happy, anim7=sad, anim8=shy
import random as _random
EMOTION_ANIM_MAP = {
    "happy":     6,   # 开心
    "sad":       7,   # 难过
    "angry":     5,   # 生气
    "surprised": 6,   # 惊讶 → 开心
    "thinking":  0,   # 思考 → 随机待机
    "sleepy":    0,   # 困倦 → 随机待机
    "excited":   6,   # 兴奋 → 开心
    "confused":  0,   # 困惑 → 随机待机
    "love":      8,   # 喜爱 → 害羞
    "neutral":   0,   # 中性 → 随机待机
    "fear":      4,   # 害怕 → 哭
    "shy":       8,   # 害羞
}

def get_anim_for_emotion(emotion: str) -> int:
    """根据情绪获取对应的动画编号，0 表示随机选一个 idle"""
    anim_id = EMOTION_ANIM_MAP.get(emotion.lower(), 0)
    if anim_id == 0:
        anim_id = _random.choice([1, 2, 3])
    return anim_id

API_KEY = os.getenv("DASHSCOPE_API_KEY", "sk-fake-placeholder")
MODEL = "paraformer-realtime-v2"
SAMPLE_RATE = 16000
AUDIO_FMT = "pcm"
CHUNK_MS = 20
BYTES_CHUNK = SAMPLE_RATE * CHUNK_MS // 1000 * 2
SILENCE_20MS = bytes(BYTES_CHUNK)

app = FastAPI()

# ====== 状态容器 ======
ui_clients: Dict[int, WebSocket] = {}
current_partial: str = ""
recent_finals: List[str] = []
RECENT_MAX = 50

camera_viewers: Set[WebSocket] = set()
esp32_camera_ws: Optional[WebSocket] = None
esp32_audio_ws: Optional[WebSocket] = None
last_frames: Deque[Tuple[float, bytes]] = deque(maxlen=10)

SCREEN_MODE_EXPRESSION = 0
SCREEN_MODE_OPENCLAW = 1
SCREEN_MODE_HOST_CAMERA = 2
SCREEN_MODE_LABELS = {
    SCREEN_MODE_EXPRESSION: "expression",
    SCREEN_MODE_OPENCLAW: "openclaw",
    SCREEN_MODE_HOST_CAMERA: "host_camera",
}
TFT_SCREEN_W = 170
TFT_SCREEN_H = 320
HOST_CAMERA_INDEX = int(os.getenv("HOST_SCREEN_CAMERA_INDEX", "0"))
HOST_CAMERA_GRADIENT_PX = 20

screen_mode: int = SCREEN_MODE_EXPRESSION
screen_mode_lock = asyncio.Lock()
screen_sender_task: Optional[asyncio.Task] = None
screen_sender_last_push_at: float = 0.0
screen_sender_last_mode: int = SCREEN_MODE_EXPRESSION

occlusion_state: Dict[str, Any] = {
    "covered": False,
    "last_check": 0.0,
    "last_switch": 0.0,
    "score": 0.0,
}

openclaw_records: Deque[str] = deque(
    [
        "等待 OpenClaw 接入",
        "任务区预留: 状态 / 事项 / 对话桥接",
        "当前为 UI 框架页，可先联调视觉效果",
    ],
    maxlen=6,
)

host_camera_capture = None
host_camera_capture_lock = threading.Lock()


def _load_font(size: int, bold: bool = False):
    if not PIL_AVAILABLE:
        return None
    font_candidates = [
        "C:/Windows/Fonts/msyhbd.ttc" if bold else "C:/Windows/Fonts/msyh.ttc",
        "C:/Windows/Fonts/simhei.ttf" if bold else "C:/Windows/Fonts/simsun.ttc",
        "C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf",
    ]
    for font_path in font_candidates:
        try:
            return ImageFont.truetype(font_path, size)
        except Exception:
            continue
    return ImageFont.load_default()


FONT_TITLE = _load_font(24, bold=True) if PIL_AVAILABLE else None
FONT_SUBTITLE = _load_font(12, bold=False) if PIL_AVAILABLE else None
FONT_CARD_TITLE = _load_font(11, bold=True) if PIL_AVAILABLE else None
FONT_CARD_VALUE = _load_font(14, bold=True) if PIL_AVAILABLE else None
FONT_BODY = _load_font(12, bold=False) if PIL_AVAILABLE else None
FONT_FOOTER = _load_font(10, bold=False) if PIL_AVAILABLE else None


def _fit_text(text: str, limit: int) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[: max(1, limit - 1)] + "…"


def _append_openclaw_record(text: str):
    clean = _fit_text(text.replace("\n", " "), 28)
    stamp = time.strftime("%H:%M:%S")
    openclaw_records.appendleft(f"{stamp}  {clean}")


def _draw_vertical_gradient(draw, width: int, height: int, top_rgb, bottom_rgb):
    for y in range(height):
        ratio = y / max(1, height - 1)
        color = tuple(
            int(top_rgb[i] * (1.0 - ratio) + bottom_rgb[i] * ratio)
            for i in range(3)
        )
        draw.line((0, y, width, y), fill=color)


def _draw_lobster_logo(draw, origin_x: int, origin_y: int):
    shell = (230, 56, 72)
    shell_dark = (150, 24, 42)
    accent = (255, 181, 186)
    draw.ellipse((origin_x + 18, origin_y + 14, origin_x + 46, origin_y + 40), fill=shell)
    draw.ellipse((origin_x + 22, origin_y + 4, origin_x + 42, origin_y + 24), fill=accent)
    draw.rounded_rectangle((origin_x + 16, origin_y + 38, origin_x + 48, origin_y + 68), radius=10, fill=shell_dark)
    draw.line((origin_x + 22, origin_y + 48, origin_x + 8, origin_y + 74), fill=shell, width=5)
    draw.line((origin_x + 42, origin_y + 48, origin_x + 56, origin_y + 74), fill=shell, width=5)
    draw.line((origin_x + 24, origin_y + 66, origin_x + 14, origin_y + 92), fill=shell_dark, width=4)
    draw.line((origin_x + 40, origin_y + 66, origin_x + 50, origin_y + 92), fill=shell_dark, width=4)
    draw.line((origin_x + 24, origin_y + 8, origin_x + 10, origin_y - 4), fill=accent, width=3)
    draw.line((origin_x + 40, origin_y + 8, origin_x + 54, origin_y - 4), fill=accent, width=3)
    draw.arc((origin_x - 10, origin_y + 8, origin_x + 20, origin_y + 36), start=270, end=110, fill=shell, width=5)
    draw.arc((origin_x + 44, origin_y + 8, origin_x + 74, origin_y + 36), start=70, end=270, fill=shell, width=5)
    draw.ellipse((origin_x + 24, origin_y + 10, origin_x + 27, origin_y + 13), fill=(18, 10, 18))
    draw.ellipse((origin_x + 37, origin_y + 10, origin_x + 40, origin_y + 13), fill=(18, 10, 18))


def render_openclaw_dashboard_image():
    if not PIL_AVAILABLE:
        return None

    img = Image.new("RGB", (TFT_SCREEN_W, TFT_SCREEN_H), (10, 10, 16))
    draw = ImageDraw.Draw(img)
    _draw_vertical_gradient(draw, TFT_SCREEN_W, TFT_SCREEN_H, (20, 16, 30), (6, 8, 18))

    draw.ellipse((-30, -20, 90, 80), fill=(80, 10, 20))
    draw.ellipse((110, 10, 220, 120), fill=(35, 12, 26))

    hero_box = (10, 12, 160, 96)
    draw.rounded_rectangle(hero_box, radius=18, fill=(22, 24, 38), outline=(133, 30, 48), width=2)
    _draw_lobster_logo(draw, 16, 20)
    draw.text((76, 24), "OPEN CLAW", font=FONT_TITLE, fill=(255, 244, 246))
    draw.text((76, 54), "龙虾任务面板", font=FONT_SUBTITLE, fill=(255, 170, 178))
    badge_text = "ONLINE" if openclaw_is_enabled() else "STANDBY"
    badge_fill = (165, 28, 44) if openclaw_is_enabled() else (92, 96, 110)
    draw.rounded_rectangle((77, 72, 146, 88), radius=8, fill=badge_fill)
    draw.text((89, 75), badge_text, font=FONT_FOOTER, fill=(255, 250, 250))

    left_card = (10, 108, 82, 162)
    right_card = (88, 108, 160, 162)
    for box in (left_card, right_card):
        draw.rounded_rectangle(box, radius=14, fill=(16, 18, 28), outline=(58, 68, 86), width=1)

    draw.text((18, 118), "STATE", font=FONT_CARD_TITLE, fill=(255, 138, 150))
    draw.text((18, 136), "未接硬件" if not openclaw_is_enabled() else "桥接启用", font=FONT_CARD_VALUE, fill=(247, 248, 255))

    draw.text((96, 118), "QUEUE", font=FONT_CARD_TITLE, fill=(255, 138, 150))
    queue_value = str(len(openclaw_records))
    draw.text((96, 136), queue_value.rjust(2, "0"), font=FONT_CARD_VALUE, fill=(247, 248, 255))

    records_box = (10, 172, 160, 306)
    draw.rounded_rectangle(records_box, radius=16, fill=(15, 18, 28), outline=(72, 76, 96), width=1)
    draw.text((18, 182), "RECORDS", font=FONT_CARD_TITLE, fill=(255, 138, 150))

    row_y = 204
    records = list(openclaw_records)[:4]
    if not records:
        records = ["等待新的事项写入"]
    for idx, item in enumerate(records):
        top = row_y + idx * 24
        draw.rounded_rectangle((18, top, 152, top + 18), radius=8, fill=(25, 28, 42))
        draw.text((24, top + 4), _fit_text(item, 24), font=FONT_FOOTER, fill=(227, 232, 244))

    footer = f"SYNC  {time.strftime('%H:%M:%S')}"
    draw.text((18, 286), footer, font=FONT_FOOTER, fill=(168, 177, 196))
    return img


def render_host_camera_placeholder(message: str):
    if not PIL_AVAILABLE:
        return None
    img = Image.new("RGB", (TFT_SCREEN_W, TFT_SCREEN_H), (250, 250, 250))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle((18, 64, 152, 256), radius=24, fill=(255, 255, 255), outline=(224, 224, 230), width=2)
    draw.rectangle((34, 96, 136, 198), outline=(180, 180, 190), width=3)
    draw.line((46, 186, 78, 150, 102, 174, 128, 126), fill=(220, 120, 120), width=4)
    draw.text((30, 220), "HOST CAMERA", font=FONT_CARD_TITLE, fill=(120, 50, 50))
    draw.text((24, 240), _fit_text(message, 18), font=FONT_BODY, fill=(80, 80, 88))
    return img


def _ensure_host_camera_capture():
    global host_camera_capture
    if not HOST_CAMERA_AVAILABLE:
        return None
    with host_camera_capture_lock:
        if host_camera_capture is not None and host_camera_capture.isOpened():
            return host_camera_capture
        cap = cv2.VideoCapture(HOST_CAMERA_INDEX, cv2.CAP_DSHOW)
        if not cap.isOpened():
            cap = cv2.VideoCapture(HOST_CAMERA_INDEX)
        if not cap.isOpened():
            return None
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        host_camera_capture = cap
        return host_camera_capture


def _release_host_camera_capture():
    global host_camera_capture
    with host_camera_capture_lock:
        if host_camera_capture is not None:
            try:
                host_camera_capture.release()
            except Exception:
                pass
            host_camera_capture = None


def render_host_camera_image():
    if not PIL_AVAILABLE:
        return None
    cap = _ensure_host_camera_capture()
    if cap is None:
        return render_host_camera_placeholder("未找到电脑摄像头")

    ok, frame = cap.read()
    if not ok or frame is None:
        _release_host_camera_capture()
        return render_host_camera_placeholder("读取摄像头失败")

    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    src = Image.fromarray(rgb)
    side = min(src.width, src.height)
    left = (src.width - side) // 2
    top = (src.height - side) // 2
    square = src.crop((left, top, left + side, top + side)).resize((TFT_SCREEN_W, TFT_SCREEN_W))

    canvas = Image.new("RGB", (TFT_SCREEN_W, TFT_SCREEN_H), (255, 255, 255))
    mask = Image.new("L", (TFT_SCREEN_W, TFT_SCREEN_W), 255)
    mask_px = mask.load()
    for y in range(TFT_SCREEN_W):
        alpha = 255
        if y < HOST_CAMERA_GRADIENT_PX:
            alpha = int(255 * y / max(1, HOST_CAMERA_GRADIENT_PX))
        elif y >= TFT_SCREEN_W - HOST_CAMERA_GRADIENT_PX:
            alpha = int(255 * (TFT_SCREEN_W - 1 - y) / max(1, HOST_CAMERA_GRADIENT_PX))
        alpha = max(0, min(255, alpha))
        for x in range(TFT_SCREEN_W):
            mask_px[x, y] = alpha

    top_y = (TFT_SCREEN_H - TFT_SCREEN_W) // 2
    canvas.paste(square, (0, top_y), mask)
    return canvas


def image_to_screen_jpeg_bytes(img) -> bytes:
    if not PIL_AVAILABLE or img is None:
        return b""
    out = io.BytesIO()
    img.convert("RGB").save(out, format="JPEG", quality=72, optimize=True)
    return out.getvalue()


def detect_camera_occlusion(frame_bytes: bytes) -> Tuple[bool, Dict[str, float]]:
    if not PIL_AVAILABLE:
        return False, {"uniformity": 0.0, "dominant_ratio": 0.0}
    try:
        img = Image.open(io.BytesIO(frame_bytes)).convert("RGB").resize((32, 32))
        stat = ImageStat.Stat(img)
        std = sum(stat.stddev) / max(1, len(stat.stddev))
        samples = list(img.getdata())
        buckets: Dict[Tuple[int, int, int], int] = {}
        for r, g, b in samples:
            key = (r // 32, g // 32, b // 32)
            buckets[key] = buckets.get(key, 0) + 1
        dominant_ratio = max(buckets.values()) / max(1, len(samples))
        covered = std < 18.0 or dominant_ratio > 0.58
        return covered, {"uniformity": round(std, 2), "dominant_ratio": round(dominant_ratio, 3)}
    except Exception:
        return False, {"uniformity": 0.0, "dominant_ratio": 0.0}


async def notify_screen_mode():
    label = SCREEN_MODE_LABELS.get(screen_mode, "unknown")
    try:
        await ui_broadcast_raw(f"SCREENMODE:{label}")
    except Exception:
        pass
    if esp32_camera_ws and esp32_camera_ws.client_state == WebSocketState.CONNECTED:
        try:
            await esp32_camera_ws.send_text(f"SCRMODE:{screen_mode}")
        except Exception:
            pass


async def sync_esp32_camera_fps():
    if esp32_camera_ws and esp32_camera_ws.client_state == WebSocketState.CONNECTED:
        fps = 0 if screen_mode == SCREEN_MODE_EXPRESSION else 2
        try:
            await esp32_camera_ws.send_text(f"SET:FPS={fps}")
            print(f"[SCREEN] camera uplink fps -> {fps}", flush=True)
        except Exception as exc:
            print(f"[SCREEN] camera fps sync failed: {exc}", flush=True)


async def set_screen_mode(new_mode: int, reason: str = ""):
    global screen_mode, screen_sender_last_push_at, screen_sender_last_mode
    async with screen_mode_lock:
        new_mode %= len(SCREEN_MODE_LABELS)
        screen_mode = new_mode
        screen_sender_last_push_at = 0.0
        screen_sender_last_mode = -1
        print(f"[SCREEN] mode -> {SCREEN_MODE_LABELS.get(screen_mode)} reason={reason or 'manual'}", flush=True)
        await notify_screen_mode()
        await sync_esp32_camera_fps()
        if screen_mode == SCREEN_MODE_EXPRESSION:
            _release_host_camera_capture()
        else:
            await push_screen_frame_once(force=True)


async def cycle_screen_mode(reason: str = ""):
    await set_screen_mode((screen_mode + 1) % len(SCREEN_MODE_LABELS), reason=reason)


async def push_screen_frame_once(force: bool = False):
    global screen_sender_last_push_at, screen_sender_last_mode

    ws = esp32_camera_ws
    if ws is None or ws.client_state != WebSocketState.CONNECTED:
        return
    if screen_mode == SCREEN_MODE_EXPRESSION:
        return

    now = time.monotonic()
    target_interval = 0.32 if screen_mode == SCREEN_MODE_HOST_CAMERA else 1.0
    if not force and screen_sender_last_mode == screen_mode and (now - screen_sender_last_push_at) < target_interval:
        return

    if screen_mode == SCREEN_MODE_OPENCLAW:
        image = await asyncio.to_thread(render_openclaw_dashboard_image)
    else:
        image = await asyncio.to_thread(render_host_camera_image)

    payload = await asyncio.to_thread(image_to_screen_jpeg_bytes, image)
    if not payload:
        return

    await ws.send_bytes(payload)
    screen_sender_last_push_at = now
    screen_sender_last_mode = screen_mode


async def screen_sender_loop():
    try:
        while True:
            try:
                await push_screen_frame_once()
            except Exception as exc:
                print(f"[SCREEN] push failed: {exc}", flush=True)
            await asyncio.sleep(0.08)
    except asyncio.CancelledError:
        raise


def ensure_screen_sender_task():
    global screen_sender_task
    if screen_sender_task is None or screen_sender_task.done():
        screen_sender_task = asyncio.create_task(screen_sender_loop())

# ====== 中断锁 ======
interrupt_lock = asyncio.Lock()

# ====== 尾巴舵机锁定（充电模式） ======
tail_servo_locked: bool = False  # 是否锁定尾巴（充电模式）
tail_servo_locked_angle: int = 90  # 锁定时尾巴保持的角度


async def ui_broadcast_raw(msg: str):
    """广播消息到所有UI客户端"""
    dead = []
    for k, ws in list(ui_clients.items()):
        try:
            await ws.send_text(msg)
        except Exception:
            dead.append(k)
    for k in dead:
        ui_clients.pop(k, None)


async def ui_broadcast_partial(text: str):
    global current_partial
    current_partial = text
    await ui_broadcast_raw("PARTIAL:" + text)


async def ui_broadcast_final(text: str):
    global current_partial, recent_finals
    current_partial = ""
    recent_finals.append(text)
    if len(recent_finals) > RECENT_MAX:
        recent_finals = recent_finals[-RECENT_MAX:]
    await ui_broadcast_raw("FINAL:" + text)
    print(f"[ASR/AI FINAL] {text}", flush=True)


async def full_system_reset(reason: str = ""):
    """回到刚启动后的状态"""
    await hard_reset_audio(reason or "full_system_reset")
    await stop_current_recognition()
    
    global current_partial, recent_finals, tail_servo_locked, tail_servo_locked_angle
    current_partial = ""
    recent_finals = []
    # 解除尾巴锁定（重置时恢复）
    tail_servo_locked = False
    tail_servo_locked_angle = 90
    
    try:
        last_frames.clear()
    except Exception:
        pass
    
    try:
        if esp32_audio_ws and (esp32_audio_ws.client_state == WebSocketState.CONNECTED):
            await esp32_audio_ws.send_text("RESET")
    except Exception:
        pass

    try:
        await set_screen_mode(SCREEN_MODE_EXPRESSION, reason="full_reset")
    except Exception:
        pass
    
    print("[SYSTEM] full reset done.", flush=True)


# ========= AI 对话启动 =========
async def start_ai_with_text(user_text: str):
    """硬重置后，开启新的 AI 语音输出。"""
    # 若启用 OpenClaw 桥接：仅当用户说"帮我xxx"时才转发到 OpenClaw
    if openclaw_is_enabled() and user_text.strip():
        trimmed = user_text.strip()
        if "帮我" in trimmed or "帮忙" in trimmed:
            openclaw_send_prompt(trimmed)
            _append_openclaw_record(trimmed)
            print(f"[OpenClaw] 匹配'帮我/帮忙'，已转发: {trimmed[:50]}{'…' if len(trimmed) > 50 else ''}", flush=True)

    async def _runner():
        txt_buf: List[str] = []
        emotion_analyzed = False

        # 组装（图像+文本）
        content_list = []
        if last_frames:
            try:
                _, jpeg_bytes = last_frames[-1]
                img_b64 = base64.b64encode(jpeg_bytes).decode("ascii")
                content_list.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"}
                })
                print(f"[AI] 使用视频流图像：{len(jpeg_bytes)} bytes", flush=True)
            except Exception as e:
                print(f"[AI] 获取视频流图像失败：{e}", flush=True)
        else:
            print("[AI] 警告：没有可用的视频帧", flush=True)
        content_list.append({"type": "text", "text": user_text})

        try:
            async for piece in stream_chat(content_list, voice="Mochi", audio_format="wav"):
                if piece.text_delta:
                    txt_buf.append(piece.text_delta)
                    full_text = "".join(txt_buf)
                    
                    if not emotion_analyzed and len(full_text) >= 10:
                        emotion_analyzed = True
                        try:
                            loop = asyncio.get_event_loop()
                            emotion = await loop.run_in_executor(None, analyze_emotion, full_text)
                            
                            if esp32_audio_ws and (esp32_audio_ws.client_state == WebSocketState.CONNECTED):
                                await esp32_audio_ws.send_text(f"EMO:{emotion}")
                                await esp32_audio_ws.send_text(f"EXPR:{emotion}")
                                # 播放对应表情动画
                                anim_id = get_anim_for_emotion(emotion)
                                await esp32_audio_ws.send_text(f"ANIM:{anim_id}")
                                # 发送AI情感动作（覆盖之前的用户情感动作）
                                tail_angle = tail_servo_locked_angle if tail_servo_locked else None
                                await send_emotion_actions(esp32_audio_ws, emotion, clear_first=True, tail_locked_angle=tail_angle)
                                print(f"[AI] 情绪分析: {emotion} -> anim{anim_id}", flush=True)
                        except Exception as e:
                            print(f"[AI] 情绪分析失败: {e}", flush=True)
                    
                    try:
                        await ui_broadcast_partial("[AI] " + full_text)
                    except Exception:
                        pass

                if piece.audio_b64:
                    try:
                        pcm24 = base64.b64decode(piece.audio_b64)
                    except Exception:
                        pcm24 = b""
                    if pcm24:
                        # TTS 为 24kHz，直传不降采样以保持音质（ESP32 已支持 24k）
                        pcm16 = audioop.mul(pcm24, 2, 0.80)
                        if pcm16:
                            if len(stream_clients) > 0:
                                await broadcast_pcm16_realtime(pcm16)
                            else:
                                print(f"[AI] Warning: No stream clients connected, audio lost ({len(pcm16)} bytes)", flush=True)

        except asyncio.CancelledError:
            raise
        except Exception as e:
            try:
                await ui_broadcast_final(f"[AI] 发生错误：{e}")
            except Exception:
                pass
        finally:
            for sc in list(stream_clients):
                if not sc.abort_event.is_set():
                    try: sc.q.put_nowait(b"\x00"*BYTES_PER_20MS_16K)
                    except Exception: pass
                    try: sc.q.put_nowait(None)
                    except Exception: pass

            final_text = ("".join(txt_buf)).strip() or "（空响应）"
            try:
                await ui_broadcast_final("[AI] " + final_text)
            except Exception:
                pass
            
            try:
                await ui_broadcast_partial("")
            except Exception:
                pass
            
            await asyncio.sleep(0.5)
            
            try:
                if esp32_audio_ws and (esp32_audio_ws.client_state == WebSocketState.CONNECTED):
                    await esp32_audio_ws.send_text("START")
                    await esp32_audio_ws.send_text("EXPR:idle")
                    print("[AI] 已通知ESP32重启ASR", flush=True)
            except Exception as e:
                print(f"[AI] 通知ESP32重启ASR失败：{e}", flush=True)

    await hard_reset_audio("start_ai_with_text")
    await stop_current_recognition()
    
    global current_partial
    current_partial = ""
    await ui_broadcast_partial("")
    
    try:
        if esp32_audio_ws and (esp32_audio_ws.client_state == WebSocketState.CONNECTED):
            await esp32_audio_ws.send_text("STOP")
            await esp32_audio_ws.send_text("TTS_START")  # 通知ESP32准备接收音频
            print("[AI] Notified ESP32 to start TTS playback", flush=True)
    except Exception:
        pass
    
    # 检查语音指令：如果用户说的话匹配预设动作，发送步态命令到ESP32
    voice_cmd_result = match_voice_command(user_text)
    if voice_cmd_result:
        gait_cmd, matched_kw = voice_cmd_result
        print(f"[VOICE CMD] 匹配到指令: '{matched_kw}' -> GAIT:{gait_cmd}", flush=True)
        try:
            if esp32_audio_ws and (esp32_audio_ws.client_state == WebSocketState.CONNECTED):
                await esp32_audio_ws.send_text(f"GAIT:{gait_cmd}")
        except Exception as e:
            print(f"[VOICE CMD] 发送步态命令失败: {e}", flush=True)
    
    # 检查动作指令（如充电）：发送舵机命令到ESP32
    voice_action_result = match_voice_action(user_text)
    if voice_action_result:
        action_cmd, matched_kw = voice_action_result
        print(f"[VOICE ACTION] 匹配到动作: '{matched_kw}' -> {action_cmd}", flush=True)
        try:
            global tail_servo_locked, tail_servo_locked_angle
            if esp32_audio_ws and (esp32_audio_ws.client_state == WebSocketState.CONNECTED):
                await esp32_audio_ws.send_text(action_cmd)
                # 根据命令设置/解除尾巴锁定
                if action_cmd == "SERVO:13,0":  # 充电：锁定尾巴在0度
                    tail_servo_locked = True
                    tail_servo_locked_angle = 0
                    print(f"[VOICE ACTION] 尾巴已锁定在 {tail_servo_locked_angle}°（充电模式）", flush=True)
                elif action_cmd == "SERVO:13,90":  # 恢复：解除锁定
                    tail_servo_locked = False
                    tail_servo_locked_angle = 90
                    print(f"[VOICE ACTION] 尾巴锁定已解除（恢复正常）", flush=True)
        except Exception as e:
            print(f"[VOICE ACTION] 发送动作命令失败: {e}", flush=True)
    
    loop = asyncio.get_running_loop()
    from audio_stream import __dict__ as _as_dict
    task = loop.create_task(_runner())
    _as_dict["current_ai_task"] = task
    
    # 并行分析用户语句的情感，并发送情感动作到ESP32
    async def _send_user_emotion_actions():
        try:
            user_emo = await loop.run_in_executor(None, analyze_emotion, user_text)
            if user_emo and esp32_audio_ws and (esp32_audio_ws.client_state == WebSocketState.CONNECTED):
                # 如果尾巴被锁定（充电模式），传入锁定角度
                tail_angle = tail_servo_locked_angle if tail_servo_locked else None
                await send_emotion_actions(esp32_audio_ws, user_emo, clear_first=True, tail_locked_angle=tail_angle)
                print(f"[EMACT] User emotion: {user_emo}", flush=True)
        except Exception as e:
            print(f"[EMACT] User emotion analysis error: {e}", flush=True)
    
    loop.create_task(_send_user_emotion_actions())


# ========= 语音复述功能 =========
async def start_tts_repeat(user_text: str):
    """直接复述用户输入的文字，不进行对话。"""
    async def _runner():
        txt_buf: List[str] = []
        # 使用极简的系统提示词，强制只复述
        repeat_system_prompt = "你是一个语音复述工具。你的唯一任务是：一字不差地复述用户说的话，不要添加、删除或修改任何字词，不要有任何其他输出。"
        
        content_list = [{"type": "text", "text": user_text}]

        try:
            async for piece in stream_chat(content_list, voice="Mochi", audio_format="wav", system_prompt=repeat_system_prompt):
                if piece.text_delta:
                    txt_buf.append(piece.text_delta)
                    full_text = "".join(txt_buf)
                    
                    try:
                        await ui_broadcast_partial("[复述] " + full_text)
                    except Exception:
                        pass

                if piece.audio_b64:
                    try:
                        pcm24 = base64.b64decode(piece.audio_b64)
                    except Exception:
                        pcm24 = b""
                    if pcm24:
                        # TTS 24kHz 直传，不降采样
                        pcm16 = audioop.mul(pcm24, 2, 0.80)
                        if pcm16:
                            if len(stream_clients) > 0:
                                await broadcast_pcm16_realtime(pcm16)
                            else:
                                print(f"[REPEAT] Warning: No stream clients connected, audio lost ({len(pcm16)} bytes)", flush=True)

        except asyncio.CancelledError:
            raise
        except Exception as e:
            try:
                await ui_broadcast_final(f"[复述] 发生错误：{e}")
            except Exception:
                pass
        finally:
            for sc in list(stream_clients):
                if not sc.abort_event.is_set():
                    try: sc.q.put_nowait(b"\x00"*BYTES_PER_20MS_16K)
                    except Exception: pass
                    try: sc.q.put_nowait(None)
                    except Exception: pass

            final_text = ("".join(txt_buf)).strip() or "（空响应）"
            try:
                await ui_broadcast_final("[复述] " + final_text)
            except Exception:
                pass
            
            try:
                await ui_broadcast_partial("")
            except Exception:
                pass
            
            await asyncio.sleep(0.5)
            
            try:
                if esp32_audio_ws and (esp32_audio_ws.client_state == WebSocketState.CONNECTED):
                    await esp32_audio_ws.send_text("START")
                    await esp32_audio_ws.send_text("EXPR:idle")
                    print("[REPEAT] 已通知ESP32重启ASR", flush=True)
            except Exception as e:
                print(f"[REPEAT] 通知ESP32重启ASR失败：{e}", flush=True)

    await hard_reset_audio("start_tts_repeat")
    await stop_current_recognition()
    
    global current_partial
    current_partial = ""
    await ui_broadcast_partial("")
    
    try:
        if esp32_audio_ws and (esp32_audio_ws.client_state == WebSocketState.CONNECTED):
            await esp32_audio_ws.send_text("STOP")
            await esp32_audio_ws.send_text("TTS_START")
            print("[REPEAT] Notified ESP32 to start TTS playback", flush=True)
    except Exception:
        pass
    
    loop = asyncio.get_running_loop()
    from audio_stream import __dict__ as _as_dict
    task = loop.create_task(_runner())
    _as_dict["current_ai_task"] = task


# ====== 页面路由 ======
@app.get("/", response_class=HTMLResponse)
def root():
    return get_index_html()


@app.get("/api/health", response_class=PlainTextResponse)
def health():
    return "OK"


# 注册 /stream.wav
register_stream_route(app)


# ====== WebSocket: UI 文本推送 ======
@app.websocket("/ws_ui")
async def ws_ui(ws: WebSocket):
    await ws.accept()
    ui_clients[id(ws)] = ws
    try:
        init = {"partial": current_partial, "finals": recent_finals[-10:]}
        await ws.send_text("INIT:" + json.dumps(init, ensure_ascii=False))
        await ws.send_text("SCREENMODE:" + SCREEN_MODE_LABELS.get(screen_mode, "expression"))
        while True:
            try:
                msg = await asyncio.wait_for(ws.receive_text(), timeout=60)
                # 步态控制命令转发
                if msg.startswith("GAIT:"):
                    gait_cmd = msg[5:].strip().upper()
                    print(f"[UI] Gait command: {gait_cmd}", flush=True)
                    if esp32_audio_ws and esp32_audio_ws.client_state == WebSocketState.CONNECTED:
                        await esp32_audio_ws.send_text(f"GAIT:{gait_cmd}")
                # 摄像头参数调节转发
                elif msg.startswith("CAMSET:"):
                    if esp32_audio_ws and esp32_audio_ws.client_state == WebSocketState.CONNECTED:
                        await esp32_audio_ws.send_text(msg)
                        print(f"[UI] Camera setting: {msg}", flush=True)
                # TFT屏幕参数调节转发
                elif msg.startswith("TFTSET:"):
                    if esp32_audio_ws and esp32_audio_ws.client_state == WebSocketState.CONNECTED:
                        await esp32_audio_ws.send_text(msg)
                        print(f"[UI] TFT setting: {msg}", flush=True)
                # 舵机直接控制转发（如充电按钮）
                elif msg.startswith("SERVO:"):
                    if esp32_audio_ws and esp32_audio_ws.client_state == WebSocketState.CONNECTED:
                        await esp32_audio_ws.send_text(msg)
                        print(f"[UI] Servo command: {msg}", flush=True)
                        # 检测充电相关命令，设置/解除尾巴锁定
                        global tail_servo_locked, tail_servo_locked_angle
                        if msg == "SERVO:13,0":  # 充电：锁定尾巴在0度
                            tail_servo_locked = True
                            tail_servo_locked_angle = 0
                            print(f"[UI] 尾巴已锁定在 {tail_servo_locked_angle}°（充电模式）", flush=True)
                        elif msg == "SERVO:13,90":  # 恢复：解除锁定
                            tail_servo_locked = False
                            tail_servo_locked_angle = 90
                            print(f"[UI] 尾巴锁定已解除（恢复正常）", flush=True)
                # 动画控制转发
                elif msg.startswith("ANIM:"):
                    if esp32_audio_ws and esp32_audio_ws.client_state == WebSocketState.CONNECTED:
                        await esp32_audio_ws.send_text(msg)
                        print(f"[UI] Animation command: {msg}", flush=True)
                # 音效控制转发
                elif msg.startswith("SND:") or msg.startswith("ANIMSND:"):
                    if esp32_audio_ws and esp32_audio_ws.client_state == WebSocketState.CONNECTED:
                        await esp32_audio_ws.send_text(msg)
                        print(f"[UI] Sound command: {msg}", flush=True)
                # 键盘事件处理
                elif msg.startswith("KEY:"):
                    key_data = msg[4:].strip()
                    await handle_keyboard_event(key_data)
                # 语音复述命令
                elif msg.startswith("REPEAT:"):
                    text = msg[7:].strip()
                    if text:
                        print(f"[UI] Repeat text: {text}", flush=True)
                        async with interrupt_lock:
                            await start_tts_repeat(text)
            except asyncio.TimeoutError:
                continue
    except WebSocketDisconnect:
        pass
    finally:
        ui_clients.pop(id(ws), None)


# 键盘状态
keyboard_state = {
    "shift": False,
    "current_gait": "STOP"
}


async def handle_keyboard_event(key_data: str):
    """处理键盘事件并转发到ESP32
    
    键盘映射:
    - W: 前进（慢走）
    - Shift+W: 快走（trot）
    - T: 快走直线（trot straight）
    - R: 跑步（run）
    - B: 后退（backward）
    - V: 四腿往复（wave）
    - S: 坐下
    - L: 倒下（laydown）
    - A: 左转
    - D: 右转
    - J: 跳跃
    - Space: 待机
    - Escape: 停止
    - C: 复位到中心
    """
    global keyboard_state
    
    parts = key_data.split(":")
    if len(parts) < 2:
        return
    
    event_type = parts[0]  # DOWN or UP
    key = parts[1].upper()
    shift = len(parts) > 2 and parts[2] == "SHIFT"
    
    keyboard_state["shift"] = shift
    
    if not esp32_audio_ws or esp32_audio_ws.client_state != WebSocketState.CONNECTED:
        return
    
    if event_type == "DOWN":
        if key == "W":
            if shift:
                await esp32_audio_ws.send_text("GAIT:TROT")
                keyboard_state["current_gait"] = "TROT"
            else:
                await esp32_audio_ws.send_text("GAIT:WALK")
                keyboard_state["current_gait"] = "WALK"
        elif key == "T":
            await esp32_audio_ws.send_text("GAIT:TROT_STRAIGHT")
            keyboard_state["current_gait"] = "TROT_STRAIGHT"
        elif key == "R":
            await esp32_audio_ws.send_text("GAIT:RUN")
            keyboard_state["current_gait"] = "RUN"
        elif key == "B":
            await esp32_audio_ws.send_text("GAIT:BACKWARD")
            keyboard_state["current_gait"] = "BACKWARD"
        elif key == "E":
            await esp32_audio_ws.send_text("GAIT:EFFICIENT_WALK")
            keyboard_state["current_gait"] = "EFFICIENT_WALK"
        elif key == "V":
            await esp32_audio_ws.send_text("GAIT:WAVE")
            keyboard_state["current_gait"] = "WAVE"
        elif key == "S":
            await esp32_audio_ws.send_text("GAIT:SIT")
            keyboard_state["current_gait"] = "SIT"
        elif key == "L":
            await esp32_audio_ws.send_text("GAIT:LAYDOWN")
            keyboard_state["current_gait"] = "LAYDOWN"
        elif key == "A":
            await esp32_audio_ws.send_text("GAIT:LEFT")
        elif key == "D":
            await esp32_audio_ws.send_text("GAIT:RIGHT")
        elif key == "J":
            await esp32_audio_ws.send_text("GAIT:JUMP")
            keyboard_state["current_gait"] = "JUMP"
        elif key == "SPACE" or key == " ":
            await esp32_audio_ws.send_text("GAIT:IDLE")
            keyboard_state["current_gait"] = "IDLE"
        elif key == "ESCAPE" or key == "ESC":
            await esp32_audio_ws.send_text("GAIT:STOP")
            keyboard_state["current_gait"] = "STOP"
        elif key == "C":
            await esp32_audio_ws.send_text("GAIT:CENTER")
            keyboard_state["current_gait"] = "STOP"
    
    elif event_type == "UP":
        if key == "A" or key == "D":
            # 松开转向键时恢复直行
            await esp32_audio_ws.send_text("GAIT:STRAIGHT")
        elif key in ("W", "R", "T", "B", "E"):
            # 松开移动键时停止
            await esp32_audio_ws.send_text("GAIT:STOP")
            keyboard_state["current_gait"] = "STOP"


# ====== WebSocket: ESP32 音频入口 ======
@app.websocket("/ws_audio")
async def ws_audio(ws: WebSocket):
    global esp32_audio_ws, screen_sender_last_push_at, screen_sender_last_mode
    esp32_audio_ws = ws
    await ws.accept()
    print("[AUDIO] ESP32 connected", flush=True)
    screen_sender_last_push_at = 0.0
    screen_sender_last_mode = -1
    
    recognition = None
    streaming = False
    last_ts = time.monotonic()
    keepalive_task: Optional[asyncio.Task] = None

    async def stop_rec(send_notice: Optional[str] = None):
        nonlocal recognition, streaming, keepalive_task
        if keepalive_task and not keepalive_task.done():
            keepalive_task.cancel()
            try:
                await keepalive_task
            except:
                pass
        keepalive_task = None
        if recognition:
            try:
                recognition.stop()
            except Exception:
                pass
            recognition = None
        await set_current_recognition(None)
        streaming = False
        if send_notice:
            try:
                await ws.send_text(send_notice)
            except Exception:
                pass

    async def on_sdk_error(_msg: str):
        await stop_rec(send_notice="RESTART")

    async def keepalive_loop():
        nonlocal last_ts, recognition, streaming
        try:
            while streaming and recognition is not None:
                idle = time.monotonic() - last_ts
                if idle > 0.35:
                    try:
                        for _ in range(30):
                            recognition.send_audio_frame(SILENCE_20MS)
                        last_ts = time.monotonic()
                    except Exception:
                        await on_sdk_error("keepalive send failed")
                        return
                await asyncio.sleep(0.10)
        except asyncio.CancelledError:
            return

    try:
        while True:
            if ws.client_state != WebSocketState.CONNECTED:
                break
            try:
                msg = await ws.receive()
            except WebSocketDisconnect:
                break
            except RuntimeError as e:
                if "Cannot call" in str(e):
                    break
                raise

            if "text" in msg and msg["text"]:
                raw = (msg["text"] or "").strip()
                cmd = raw.upper()

                if cmd == "START":
                    print("[AUDIO] START received from ESP32", flush=True)
                    print(f"[AUDIO] ASR_AVAILABLE={ASR_AVAILABLE}, API_KEY={'***'+API_KEY[-4:] if API_KEY else 'None'}", flush=True)
                    await stop_rec()
                    await ui_broadcast_partial("")
                    global current_partial
                    current_partial = ""
                    await asyncio.sleep(0.3)

                    if ASR_AVAILABLE and API_KEY:
                        loop = asyncio.get_running_loop()
                        def post(coro):
                            asyncio.run_coroutine_threadsafe(coro, loop)

                        cb = ASRCallback(
                            on_sdk_error=lambda s: post(on_sdk_error(s)),
                            post=post,
                            ui_broadcast_partial=ui_broadcast_partial,
                            ui_broadcast_final=ui_broadcast_final,
                            is_playing_now_fn=is_playing_now,
                            start_ai_with_text_fn=start_ai_with_text,
                            full_system_reset_fn=full_system_reset,
                            interrupt_lock=interrupt_lock,
                        )

                        try:
                            print("[AUDIO] Creating DashScope ASR Recognition...", flush=True)
                            recognition = dash_audio.asr.Recognition(
                                api_key=API_KEY,
                                model=MODEL,
                                format=AUDIO_FMT,
                                sample_rate=SAMPLE_RATE,
                                callback=cb
                            )
                            recognition.start()
                            await set_current_recognition(recognition)
                            streaming = True
                            last_ts = time.monotonic()
                            keepalive_task = asyncio.create_task(keepalive_loop())
                            print("[AUDIO] ASR Recognition started successfully!", flush=True)
                            await ui_broadcast_partial("（已开始接收音频…）")
                        except Exception as e:
                            print(f"[AUDIO] ASR Recognition failed: {e}", flush=True)
                            import traceback
                            traceback.print_exc()
                            await ui_broadcast_partial(f"（ASR启动失败：{e}）")
                    else:
                        print(f"[AUDIO] ASR not available! ASR_AVAILABLE={ASR_AVAILABLE}, API_KEY={bool(API_KEY)}", flush=True)
                        await ui_broadcast_partial("（ASR未配置，请检查dashscope安装和API_KEY）")

                    await ws.send_text("OK:STARTED")

                elif cmd == "STOP":
                    if recognition:
                        for _ in range(15):
                            try:
                                recognition.send_audio_frame(SILENCE_20MS)
                            except Exception:
                                break
                    await stop_rec(send_notice="OK:STOPPED")

                elif raw.startswith("PROMPT:"):
                    text = raw[7:].strip()
                    if text:
                        async with interrupt_lock:
                            await start_ai_with_text(text)
                        await ws.send_text("OK:PROMPT_ACCEPTED")

            elif "bytes" in msg and msg["bytes"]:
                if is_playing_now():
                    continue
                if streaming and recognition:
                    try:
                        recognition.send_audio_frame(msg["bytes"])
                        last_ts = time.monotonic()
                    except Exception:
                        await on_sdk_error("send_audio_frame failed")

    except Exception as e:
        print(f"[AUDIO] Error: {e}", flush=True)
    finally:
        await stop_rec()
        if esp32_audio_ws is ws:
            esp32_audio_ws = None
        _release_host_camera_capture()
        print("[AUDIO] Disconnected", flush=True)


# ====== WebSocket: ESP32 摄像头入口 ======
@app.websocket("/ws/camera")
async def ws_camera_esp(ws: WebSocket):
    global esp32_camera_ws
    # 如果旧连接存在但已断开，清理它
    if esp32_camera_ws is not None:
        try:
            if esp32_camera_ws.client_state != WebSocketState.CONNECTED:
                esp32_camera_ws = None
            else:
                # 旧连接仍然活跃，拒绝新连接
                print("[CAMERA] Rejecting new connection, existing connection still active", flush=True)
                await ws.close(code=1013)
                return
        except Exception:
            esp32_camera_ws = None
    
    esp32_camera_ws = ws
    await ws.accept()
    print("[CAMERA] ESP32 connected", flush=True)
    ensure_screen_sender_task()
    await notify_screen_mode()
    await sync_esp32_camera_fps()
    
    try:
        while True:
            msg = await ws.receive()
            
            if "bytes" in msg and msg["bytes"]:
                data = msg["bytes"]
                last_frames.append((time.time(), data))

                now_mono = time.monotonic()
                if now_mono - float(occlusion_state["last_check"]) >= 0.35:
                    occlusion_state["last_check"] = now_mono
                    covered, metrics = await asyncio.to_thread(detect_camera_occlusion, data)
                    occlusion_state["score"] = metrics.get("dominant_ratio", 0.0)
                    if covered and not bool(occlusion_state["covered"]):
                        if now_mono - float(occlusion_state["last_switch"]) >= 1.2:
                            occlusion_state["last_switch"] = now_mono
                            await cycle_screen_mode(reason=f"camera_covered:{metrics}")
                    occlusion_state["covered"] = covered
                
                if camera_viewers:
                    dead = []
                    for viewer_ws in list(camera_viewers):
                        try:
                            await viewer_ws.send_bytes(data)
                        except Exception:
                            dead.append(viewer_ws)
                    for v in dead:
                        camera_viewers.discard(v)
            
            elif msg.get("type") in ("websocket.close", "websocket.disconnect"):
                break
    
    except WebSocketDisconnect:
        pass
    except Exception as e:
        print(f"[CAMERA] Error: {e}", flush=True)
    finally:
        esp32_camera_ws = None
        print("[CAMERA] Disconnected", flush=True)


# ====== WebSocket: 浏览器观看视频 ======
@app.websocket("/ws/viewer")
async def ws_viewer(ws: WebSocket):
    await ws.accept()
    camera_viewers.add(ws)
    print(f"[VIEWER] Connected. Total: {len(camera_viewers)}", flush=True)
    
    try:
        while True:
            await asyncio.sleep(60)
    except WebSocketDisconnect:
        pass
    finally:
        camera_viewers.discard(ws)
        print(f"[VIEWER] Disconnected. Total: {len(camera_viewers)}", flush=True)


# ====== HTTP: 硬件控制 API ======
@app.get("/servo")
async def servo_control(ch: int = 0, angle: int = 90):
    """PCA9685 舵机控制"""
    if esp32_audio_ws and esp32_audio_ws.client_state == WebSocketState.CONNECTED:
        await esp32_audio_ws.send_text(f"SERVO:{ch},{angle}")
        return {"ok": True, "ch": ch, "angle": angle}
    return {"ok": False, "error": "ESP32 not connected"}


@app.get("/sts")
async def sts_control(id: int = 1, pos: int = 2048, scan: int = 0):
    """STS3032 舵机控制"""
    if esp32_audio_ws and esp32_audio_ws.client_state == WebSocketState.CONNECTED:
        if scan:
            await esp32_audio_ws.send_text("STS:SCAN")
            return {"ok": True, "action": "scan"}
        await esp32_audio_ws.send_text(f"STS:{id},{pos}")
        return {"ok": True, "id": id, "pos": pos}
    return {"ok": False, "error": "ESP32 not connected"}


@app.get("/screen")
async def screen_test(cmd: str = "color", r: int = 255, g: int = 0, b: int = 0):
    """屏幕测试"""
    if esp32_audio_ws and esp32_audio_ws.client_state == WebSocketState.CONNECTED:
        await esp32_audio_ws.send_text(f"SCREEN:{cmd},{r},{g},{b}")
        return {"ok": True, "cmd": cmd}
    return {"ok": False, "error": "ESP32 not connected"}


@app.get("/screen_mode")
async def screen_mode_control(mode: Optional[int] = None, next_mode: int = 0):
    """查看或切换屏幕模式

    mode: 0=expression, 1=openclaw, 2=host_camera
    next_mode: 1 时循环切换一次
    """
    if next_mode:
        await cycle_screen_mode(reason="http_next")
    elif mode is not None:
        await set_screen_mode(mode, reason="http_set")
    return {
        "ok": True,
        "mode": screen_mode,
        "label": SCREEN_MODE_LABELS.get(screen_mode, "unknown"),
        "occlusion": {
            "covered": bool(occlusion_state["covered"]),
            "score": occlusion_state["score"],
        },
        "openclaw_records": list(openclaw_records),
    }


@app.get("/camera_setting")
async def camera_setting(param: str = "", value: int = 0):
    """摄像头参数调节
    
    param: brightness(-2~2), contrast(-2~2), saturation(-2~2), 
           sharpness(-2~2), wb_mode(0~4), ae_level(-2~2),
           special_effect(0~6), hmirror(0/1), vflip(0/1)
    value: 参数值
    """
    if esp32_audio_ws and esp32_audio_ws.client_state == WebSocketState.CONNECTED:
        await esp32_audio_ws.send_text(f"CAMSET:{param},{value}")
        return {"ok": True, "param": param, "value": value}
    return {"ok": False, "error": "ESP32 not connected"}


@app.get("/tft_setting")
async def tft_setting(param: str = "", value: int = 0):
    """TFT屏幕参数调节 (ST7789)
    
    param: brightness(0~255), invert(0/1), gamma(1/2/4/8),
           display(0/1), rotation(0~3), cabc(0~3)
    value: 参数值
    """
    if esp32_audio_ws and esp32_audio_ws.client_state == WebSocketState.CONNECTED:
        await esp32_audio_ws.send_text(f"TFTSET:{param},{value}")
        return {"ok": True, "param": param, "value": value}
    return {"ok": False, "error": "ESP32 not connected"}


@app.get("/audio_test")
async def audio_test(freq: int = 1000, dur: int = 500):
    """音频测试"""
    if esp32_audio_ws and esp32_audio_ws.client_state == WebSocketState.CONNECTED:
        await esp32_audio_ws.send_text(f"AUDIO_TEST:{freq},{dur}")
        return {"ok": True, "freq": freq, "dur": dur}
    return {"ok": False, "error": "ESP32 not connected"}


@app.get("/anim")
async def anim_control(play: int = 0, stop: int = 0, loop: int = 0, sound: int = 0):
    """动画控制（可选配音效同步播放）
    
    play: 动画编号
    stop: 1=停止
    loop: 1=循环
    sound: 音效编号（>0时使用ANIMSND同步播放）
    """
    if esp32_audio_ws and esp32_audio_ws.client_state == WebSocketState.CONNECTED:
        if stop:
            await esp32_audio_ws.send_text("ANIM:STOP")
            await esp32_audio_ws.send_text("SND:STOP")
            return {"ok": True, "action": "stop"}
        if loop:
            await esp32_audio_ws.send_text("ANIM:LOOP")
            return {"ok": True, "action": "loop"}
        if play and sound:
            await esp32_audio_ws.send_text(f"ANIMSND:{play},{sound}")
            return {"ok": True, "play": play, "sound": sound, "mode": "animsnd"}
        await esp32_audio_ws.send_text(f"ANIM:{play}")
        return {"ok": True, "play": play}
    return {"ok": False, "error": "ESP32 not connected"}


@app.get("/sound")
async def sound_control(play: int = 0, stop: int = 0):
    """音效控制
    
    play: 音效编号
    stop: 1=停止
    """
    if esp32_audio_ws and esp32_audio_ws.client_state == WebSocketState.CONNECTED:
        if stop:
            await esp32_audio_ws.send_text("SND:STOP")
            return {"ok": True, "action": "stop"}
        if play:
            await esp32_audio_ws.send_text(f"SND:{play}")
            return {"ok": True, "play": play}
        return {"ok": False, "error": "Missing play parameter"}
    return {"ok": False, "error": "ESP32 not connected"}


@app.get("/gait")
async def gait_control(mode: str = "", turn: float = None):
    """步态控制 API
    
    mode: WALK, TROT, TROT_STRAIGHT, RUN, BACKWARD, EFFICIENT_WALK, WAVE, IDLE, SIT, LAYDOWN, NEWYEAR, STUMBLE, JUMP, STOP, CENTER
    turn: -1.0 (左转) 到 1.0 (右转)
    """
    if esp32_audio_ws and esp32_audio_ws.client_state == WebSocketState.CONNECTED:
        if mode:
            await esp32_audio_ws.send_text(f"GAIT:{mode.upper()}")
            return {"ok": True, "mode": mode.upper()}
        if turn is not None:
            await esp32_audio_ws.send_text(f"GAIT:TURN:{turn}")
            return {"ok": True, "turn": turn}
        return {"ok": True, "status": "no action"}
    return {"ok": False, "error": "ESP32 not connected"}


def get_index_html():
    return """<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>ESP32 Voice Assistant + Horse Control</title>
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body { 
      font-family: 'Segoe UI', 'Microsoft YaHei', sans-serif; 
      background: linear-gradient(135deg, #0f0c29 0%, #302b63 50%, #24243e 100%); 
      color: #eee; 
      min-height: 100vh; 
      padding: 15px; 
    }
    h1 { 
      color: #ffd700; 
      text-align: center; 
      margin-bottom: 20px; 
      font-size: 26px; 
      text-shadow: 0 2px 15px rgba(255,215,0,0.4); 
    }
    .container { 
      display: grid; 
      grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); 
      gap: 15px; 
      max-width: 1600px; 
      margin: 0 auto; 
    }
    .panel { 
      background: rgba(22, 33, 62, 0.95); 
      border-radius: 12px; 
      padding: 15px; 
      box-shadow: 0 4px 20px rgba(0,0,0,0.4); 
      border: 1px solid rgba(255,255,255,0.08); 
    }
    .panel h2 { 
      color: #00d4ff; 
      font-size: 15px; 
      border-bottom: 2px solid rgba(0,212,255,0.3); 
      padding-bottom: 8px; 
      margin-bottom: 12px; 
    }
    #videoCanvas { 
      width: 100%; 
      border-radius: 8px; 
      background: #000; 
    }
    .status { 
      font-size: 12px; 
      padding: 4px 8px; 
      border-radius: 12px; 
      display: inline-block; 
      margin-top: 8px; 
    }
    .status.online { color: #00ff88; background: rgba(0,255,136,0.15); }
    .status.offline { color: #ff4757; background: rgba(255,71,87,0.15); }
    .chat-box { 
      background: #0a0a1a; 
      border-radius: 8px; 
      padding: 12px; 
      min-height: 180px; 
      max-height: 280px; 
      overflow-y: auto; 
      font-size: 13px; 
      line-height: 1.6; 
    }
    .chat-partial { color: #888; font-style: italic; }
    .chat-final { color: #fff; margin-bottom: 8px; padding: 4px 0; border-bottom: 1px solid #222; }
    .chat-ai { color: #00d4ff; margin-bottom: 8px; padding: 4px 0; border-bottom: 1px solid #223; }
    .btn-group { display: flex; gap: 8px; margin-top: 10px; flex-wrap: wrap; }
    .btn { 
      background: linear-gradient(135deg, #ffd700, #ff9500); 
      color: #000; 
      border: none; 
      padding: 8px 16px; 
      border-radius: 6px; 
      cursor: pointer; 
      font-weight: bold; 
      font-size: 12px; 
      transition: all 0.2s; 
    }
    .btn:hover { transform: translateY(-2px); box-shadow: 0 4px 15px rgba(255,215,0,0.4); }
    .btn:active { transform: translateY(0); }
    .btn-blue { background: linear-gradient(135deg, #00d4ff, #0099cc); }
    .btn-green { background: linear-gradient(135deg, #00ff88, #00cc6a); }
    .btn-red { background: linear-gradient(135deg, #ff4757, #cc3344); color: #fff; }
    .btn-purple { background: linear-gradient(135deg, #a855f7, #7c3aed); color: #fff; }
    .btn-orange { background: linear-gradient(135deg, #ff6b35, #f7931e); color: #fff; }
    .btn-sm { padding: 6px 12px; font-size: 11px; }
    
    .servo-row { display: flex; align-items: center; margin-bottom: 8px; }
    .servo-label { width: 60px; font-size: 11px; color: #aaa; }
    .servo-slider { flex: 1; margin: 0 8px; accent-color: #ffd700; height: 6px; }
    .servo-value { width: 40px; text-align: center; font-size: 11px; color: #ffd700; }
    
    .expr-btns, .test-btns, .gait-btns { display: flex; flex-wrap: wrap; gap: 6px; }
    .expr-btn { padding: 6px 14px; font-size: 11px; border-radius: 15px; }
    
    .color-btns { display: flex; gap: 6px; flex-wrap: wrap; margin-top: 8px; }
    .color-btn { 
      width: 32px; 
      height: 32px; 
      border: 2px solid #fff; 
      border-radius: 50%; 
      cursor: pointer; 
      transition: transform 0.2s; 
    }
    .color-btn:hover { transform: scale(1.15); }

    .section-title { 
      font-size: 12px; 
      color: #888; 
      margin: 10px 0 6px 0; 
      border-top: 1px solid #333; 
      padding-top: 10px; 
    }
    
    /* 步态控制面板样式 */
    .gait-panel { grid-column: span 2; }
    @media (max-width: 700px) { .gait-panel { grid-column: span 1; } }
    
    .keyboard-hint {
      background: rgba(0,0,0,0.4);
      border-radius: 8px;
      padding: 12px;
      margin-top: 12px;
      font-size: 12px;
      line-height: 1.8;
    }
    .keyboard-hint kbd {
      background: #333;
      border: 1px solid #555;
      border-radius: 4px;
      padding: 2px 6px;
      font-family: monospace;
      margin: 0 2px;
    }
    .keyboard-hint .key-row {
      display: flex;
      gap: 20px;
      flex-wrap: wrap;
    }
    .keyboard-hint .key-item {
      display: flex;
      align-items: center;
      gap: 6px;
    }
    
    .gait-status {
      background: rgba(0,212,255,0.1);
      border: 1px solid rgba(0,212,255,0.3);
      border-radius: 8px;
      padding: 10px 15px;
      margin-top: 10px;
      display: flex;
      gap: 20px;
      font-size: 13px;
    }
    .gait-status .label { color: #888; }
    .gait-status .value { color: #00d4ff; font-weight: bold; }
    
    .active-key {
      background: #ffd700 !important;
      color: #000 !important;
      transform: scale(1.1);
    }
  </style>
</head>
<body>
  <h1>🐴 ESP32 Horse Robot Controller</h1>
  
  <div class="container">
    <!-- Camera Panel -->
    <div class="panel">
      <h2>📹 Live Camera</h2>
      <canvas id="videoCanvas" width="320" height="240"></canvas>
      <div class="btn-group">
        <button class="btn btn-blue btn-sm" onclick="connectCamera()">Connect</button>
        <button class="btn btn-red btn-sm" onclick="disconnectCamera()">Disconnect</button>
        <span class="status" id="camStatus">Connecting...</span>
      </div>
    </div>
    
    <!-- Voice Chat Panel -->
    <div class="panel">
      <h2>🎤 Voice Chat</h2>
      <div class="chat-box" id="chatBox">
        <div class="chat-partial" id="partialText">等待语音输入...</div>
      </div>
      <div class="btn-group">
        <button class="btn btn-green btn-sm" onclick="connectUI()">Connect</button>
        <button class="btn btn-red btn-sm" onclick="disconnectUI()">Disconnect</button>
        <span class="status" id="uiStatus">Disconnected</span>
      </div>
    </div>
    
    <!-- TTS Repeat Panel (语音复述) -->
    <div class="panel">
      <h2>🔊 语音复述</h2>
      <div style="margin-bottom:10px;">
        <textarea id="repeatText" 
                  placeholder="输入要复述的文字..." 
                  style="width:100%;min-height:80px;padding:8px;border-radius:6px;border:1px solid #444;background:#1a1a2e;color:#eee;font-size:13px;resize:vertical;font-family:inherit;"></textarea>
      </div>
      <div class="btn-group">
        <button class="btn btn-blue" onclick="sendRepeat()">🗣️ 开始复述</button>
        <button class="btn btn-sm" onclick="clearRepeatText()">🗑️ 清空</button>
      </div>
      <div style="margin-top:8px;font-size:11px;color:#888;">
        💡 提示: 输入文字后点击"开始复述"，机器人会说出一模一样的内容
      </div>
    </div>
    
    <!-- 步态控制面板 (Gait Control Panel) -->
    <div class="panel gait-panel">
      <h2>🦿 Leg Control (键盘控制)</h2>
      
      <div class="gait-btns">
        <button class="btn btn-green" id="btn-walk" onclick="sendGait('WALK')">🚶 慢走 (W)</button>
        <button class="btn btn-blue" id="btn-trot" onclick="sendGait('TROT')">🏃 快走 (Shift+W)</button>
        <button class="btn btn-blue" id="btn-ts" onclick="sendGait('TROT_STRAIGHT')" style="background:linear-gradient(135deg,#00bcd4,#0097a7)">🛤️ 直线走 (T)</button>
        <button class="btn btn-purple" id="btn-run" onclick="sendGait('RUN')">🐎 跑步 (R)</button>
        <button class="btn btn-orange" id="btn-backward" onclick="sendGait('BACKWARD')">⬅️ 后退 (B)</button>
        <button class="btn btn-green" id="btn-eff" onclick="sendGait('EFFICIENT_WALK')" style="background:linear-gradient(135deg,#2e7d32,#1b5e20);color:#fff">⚡ 效率走 (E)</button>
        <button class="btn" id="btn-wave" onclick="sendGait('WAVE')" style="background:linear-gradient(135deg,#e91e63,#c2185b);color:#fff">🌊 往复 (V)</button>
        <button class="btn" id="btn-idle" onclick="sendGait('IDLE')">🧘 待机 (Space)</button>
        <button class="btn btn-orange" id="btn-sit" onclick="sendGait('SIT')">🪑 坐下 (S)</button>
        <button class="btn" id="btn-laydown" onclick="sendGait('LAYDOWN')" style="background:linear-gradient(135deg,#795548,#5d4037);color:#fff">🛌 倒下 (L)</button>
        <button class="btn" id="btn-newyear" onclick="sendGait('NEWYEAR')" style="background:linear-gradient(135deg,#ff6b6b,#ee5a6f);color:#fff">🧧 拜年</button>
        <button class="btn" id="btn-stumble" onclick="sendGait('STUMBLE')" style="background:linear-gradient(135deg,#ff9800,#f57c00);color:#fff">💥 马失前蹄</button>
        <button class="btn" id="btn-jump" onclick="sendGait('JUMP')">🦘 跳跃 (J)</button>
        <button class="btn btn-red" id="btn-stop" onclick="sendGait('STOP')">⏹️ 停止 (Esc)</button>
        <button class="btn btn-sm" onclick="sendGait('CENTER')">🎯 复位 (C)</button>
      </div>
      
      <div class="gait-status">
        <div><span class="label">当前步态:</span> <span class="value" id="gaitMode">STOP</span></div>
        <div><span class="label">转向:</span> <span class="value" id="turnDir">直行</span></div>
        <div><span class="label">按键:</span> <span class="value" id="activeKeys">-</span></div>
      </div>
      
      <div class="keyboard-hint">
        <div class="key-row">
          <div class="key-item"><kbd id="key-w">W</kbd> 慢走</div>
          <div class="key-item"><kbd>Shift</kbd>+<kbd>W</kbd> 快走</div>
          <div class="key-item"><kbd id="key-t">T</kbd> 直线走</div>
          <div class="key-item"><kbd id="key-e">E</kbd> 效率走</div>
          <div class="key-item"><kbd id="key-r">R</kbd> 跑步</div>
        </div>
        <div class="key-row">
          <div class="key-item"><kbd id="key-a">A</kbd> 左转</div>
          <div class="key-item"><kbd id="key-d">D</kbd> 右转</div>
          <div class="key-item"><kbd id="key-b">B</kbd> 后退</div>
        </div>
        <div class="key-row">
          <div class="key-item"><kbd id="key-s">S</kbd> 坐下</div>
          <div class="key-item"><kbd id="key-l">L</kbd> 倒下</div>
          <div class="key-item"><kbd id="key-j">J</kbd> 跳跃</div>
          <div class="key-item"><kbd id="key-v">V</kbd> 往复</div>
        </div>
        <div class="key-row">
          <div class="key-item"><kbd id="key-space">Space</kbd> 待机</div>
          <div class="key-item"><kbd id="key-esc">Esc</kbd> 停止</div>
          <div class="key-item"><kbd id="key-c">C</kbd> 复位</div>
        </div>
      </div>
    </div>
    
    <!-- 充电控制面板 -->
    <div class="panel">
      <h2>🔋 充电控制</h2>
      <div class="btn-group">
        <button class="btn btn-green" id="btn-charge" onclick="sendCharge(true)" style="font-size:16px;padding:12px 24px;">🔌 充电（尾巴打开）</button>
        <button class="btn btn-orange" id="btn-charge-off" onclick="sendCharge(false)" style="font-size:16px;padding:12px 24px;">🔋 恢复（尾巴关闭）</button>
      </div>
      <div style="margin-top:10px;padding:8px;background:rgba(0,0,0,0.3);border-radius:6px;font-size:12px;color:#aaa;">
        <div>当前状态: <span id="chargeStatus" style="color:#00d4ff;">未充电</span></div>
        <div style="margin-top:4px;">说明: 点击"充电"尾巴转90°露出充电口，语音说"充电"也可触发</div>
      </div>
    </div>
    
    <!-- Mouth, Ears & Tail Control -->
    <div class="panel">
      <h2>🐴 Mouth, Ears & Tail</h2>
      <div id="earTailControls"></div>
    </div>
    
    <!-- PCA9685 Servo Control -->
    <div class="panel">
      <h2>🎚️ PCA9685 Servos</h2>
      <div id="pcaServoControls"></div>
    </div>
    
    <!-- STS3032 Servo Control -->
    <div class="panel">
      <h2>⚙️ STS3032 Legs</h2>
      <div id="stsServoControls"></div>
    </div>
    
    <!-- Camera Settings Panel -->
    <div class="panel">
      <h2>📷 摄像头调节</h2>
      <div id="camSettings"></div>
      <div class="section-title">白平衡模式</div>
      <div class="btn-group" style="margin-top:6px;">
        <button class="btn btn-sm" onclick="setCamSetting('wb_mode',0)" id="wb-0" style="background:#666;color:#fff">自动</button>
        <button class="btn btn-sm" onclick="setCamSetting('wb_mode',1)" id="wb-1" style="background:#ff9;color:#333">晴天</button>
        <button class="btn btn-sm" onclick="setCamSetting('wb_mode',2)" id="wb-2" style="background:#aac;color:#333">阴天</button>
        <button class="btn btn-sm" onclick="setCamSetting('wb_mode',3)" id="wb-3" style="background:#ffe;color:#333">办公</button>
        <button class="btn btn-sm" onclick="setCamSetting('wb_mode',4)" id="wb-4" style="background:#fea;color:#333">家庭</button>
      </div>
      <div class="section-title">特效</div>
      <div class="btn-group" style="margin-top:6px;">
        <button class="btn btn-sm" onclick="setCamSetting('special_effect',0)" style="background:#666;color:#fff">无</button>
        <button class="btn btn-sm" onclick="setCamSetting('special_effect',1)" style="background:#333;color:#fff">负片</button>
        <button class="btn btn-sm" onclick="setCamSetting('special_effect',2)" style="background:#888;color:#fff">黑白</button>
        <button class="btn btn-sm" onclick="setCamSetting('special_effect',3)" style="background:#c33;color:#fff">红</button>
        <button class="btn btn-sm" onclick="setCamSetting('special_effect',4)" style="background:#3a3;color:#fff">绿</button>
        <button class="btn btn-sm" onclick="setCamSetting('special_effect',5)" style="background:#33c;color:#fff">蓝</button>
        <button class="btn btn-sm" onclick="setCamSetting('special_effect',6)" style="background:#a86;color:#fff">复古</button>
      </div>
      <div class="section-title">翻转</div>
      <div class="btn-group" style="margin-top:6px;">
        <label style="color:#aaa;font-size:12px;display:flex;align-items:center;gap:4px;">
          <input type="checkbox" id="cam-hmirror" checked onchange="setCamSetting('hmirror',this.checked?1:0)"> 水平镜像
        </label>
        <label style="color:#aaa;font-size:12px;display:flex;align-items:center;gap:4px;">
          <input type="checkbox" id="cam-vflip" onchange="setCamSetting('vflip',this.checked?1:0)"> 垂直翻转
        </label>
      </div>
    </div>
    
    <!-- TFT Screen Settings Panel -->
    <div class="panel">
      <h2>🖥️ TFT屏幕 (ST7789)</h2>
      <div id="tftSettings"></div>
      <div class="section-title">Gamma曲线 (视觉对比度)</div>
      <div class="btn-group" style="margin-top:6px;">
        <button class="btn btn-sm" onclick="setTftSetting('gamma',8)" style="background:#555;color:#fff">G1.0 亮</button>
        <button class="btn btn-sm" onclick="setTftSetting('gamma',2)" style="background:#666;color:#fff">G1.8 柔和</button>
        <button class="btn btn-sm" onclick="setTftSetting('gamma',1)" style="background:#777;color:#fff">G2.2 默认</button>
        <button class="btn btn-sm" onclick="setTftSetting('gamma',4)" style="background:#888;color:#fff">G2.5 深沉</button>
      </div>
      <div class="section-title">CABC 自适应亮度</div>
      <div class="btn-group" style="margin-top:6px;">
        <button class="btn btn-sm" onclick="setTftSetting('cabc',0)" style="background:#666;color:#fff">关闭</button>
        <button class="btn btn-sm" onclick="setTftSetting('cabc',1)" style="background:#4a9;color:#fff">UI模式</button>
        <button class="btn btn-sm" onclick="setTftSetting('cabc',2)" style="background:#49a;color:#fff">静态图</button>
        <button class="btn btn-sm" onclick="setTftSetting('cabc',3)" style="background:#94a;color:#fff">视频</button>
      </div>
      <div class="section-title">其他</div>
      <div class="btn-group" style="margin-top:6px;">
        <label style="color:#aaa;font-size:12px;display:flex;align-items:center;gap:4px;">
          <input type="checkbox" id="tft-invert" onchange="setTftSetting('invert',this.checked?1:0)"> 反色显示
        </label>
        <button class="btn btn-sm btn-red" onclick="setTftSetting('display',0)">关屏</button>
        <button class="btn btn-sm btn-green" onclick="setTftSetting('display',1)">开屏</button>
      </div>
      <p style="color:#666;font-size:11px;margin-top:10px;line-height:1.4;">
        ⚠️ ST7789 硬件限制：亮度SPI命令(0x51)在部分模块上可能无效（取决于背光电路）。
        色温和饱和度不支持硬件调节。Gamma曲线可改变视觉对比度效果。
      </p>
    </div>
    
    <!-- Animation Control Panel -->
    <div class="panel">
      <h2>🎬 屏幕动画控制</h2>
      <div class="gait-btns" style="margin-top:10px;">
        <button class="btn btn-blue" onclick="playAnimation(1)" id="anim-btn-1">🧘 待机1</button>
        <button class="btn btn-blue" onclick="playAnimation(2)" id="anim-btn-2">🧘 待机2</button>
        <button class="btn btn-blue" onclick="playAnimation(3)" id="anim-btn-3">🧘 待机3</button>
        <button class="btn btn-blue" onclick="playAnimation(4)" id="anim-btn-4">😢 哭</button>
        <button class="btn btn-blue" onclick="playAnimation(5)" id="anim-btn-5">😠 生气</button>
        <button class="btn btn-blue" onclick="playAnimation(6)" id="anim-btn-6">😄 开心</button>
        <button class="btn btn-blue" onclick="playAnimation(7)" id="anim-btn-7">😞 难过</button>
        <button class="btn btn-blue" onclick="playAnimation(8)" id="anim-btn-8">😊 害羞</button>
      </div>
      <div class="btn-group" style="margin-top:10px;">
        <button class="btn btn-green btn-sm" onclick="playAnimationLoop()">🎲 随机播放</button>
        <button class="btn btn-red btn-sm" onclick="stopAnimation()">⏹️ 停止</button>
      </div>
      <div style="margin-top:10px;padding:8px;background:rgba(0,0,0,0.3);border-radius:6px;font-size:12px;color:#aaa;">
        <div>当前状态: <span id="animStatus" style="color:#00d4ff;">未播放</span></div>
        <div style="margin-top:4px;">说明: 点击按钮播放对应表情动画</div>
        <div style="margin-top:4px;color:#666;">随机播放: 待机(1/2/3)60% 开心+害羞(6/8)30% 哭+生气+难过(4/5/7)10%</div>
      </div>
    </div>
  </div>

<script>
var ws_viewer = null;
var ws_ui = null;
var canvas = document.getElementById('videoCanvas');
var ctx = canvas.getContext('2d');
var camStatus = document.getElementById('camStatus');
var uiStatus = document.getElementById('uiStatus');
var chatBox = document.getElementById('chatBox');
var partialText = document.getElementById('partialText');

// 步态控制状态
var currentGait = 'STOP';
var pressedKeys = new Set();

// 动画循环播放状态
var animLoopInterval = null;
var animLoopActive = false;
var animFrames = {}; // 存储每个动画的帧数
var FRAME_DELAY_MS = 15; // ESP32端每帧15ms

// Mouth, Ears & Tail controls (PCA9685 CH12=Mouth, CH13=Tail, CH14=Left Ear, CH15=Right Ear)
var earTailContainer = document.getElementById('earTailControls');
var earTailConfig = [
  {ch: 12, label: '👄 嘴巴', defaultVal: 90},
  {ch: 13, label: '🦴 尾巴', defaultVal: 90},
  {ch: 14, label: '👂 左耳', defaultVal: 90},
  {ch: 15, label: '👂 右耳', defaultVal: 90}
];
earTailConfig.forEach(function(cfg) {
  var row = document.createElement('div');
  row.className = 'servo-row';
  row.innerHTML = '<span class="servo-label">' + cfg.label + '</span>' +
    '<input type="range" class="servo-slider" min="0" max="180" value="' + cfg.defaultVal + '" id="pca_slider_' + cfg.ch + '">' +
    '<span class="servo-value" id="pca_val_' + cfg.ch + '">' + cfg.defaultVal + '</span>';
  earTailContainer.appendChild(row);
  row.querySelector('input').oninput = function() {
    var v = this.value;
    document.getElementById('pca_val_' + cfg.ch).textContent = v;
    fetch('/servo?ch=' + cfg.ch + '&angle=' + v);
  };
});

// PCA9685 Servo controls
var pcaContainer = document.getElementById('pcaServoControls');
var pcaLabels = ['Eye UD', 'Eye LR', 'Mouth', 'Lid L', 'Lid R', 'Rot L', 'Rot R', 'Wing'];
for (var i = 0; i < 8; i++) {
  (function(idx) {
    var row = document.createElement('div');
    row.className = 'servo-row';
    row.innerHTML = '<span class="servo-label">' + pcaLabels[idx] + '</span>' +
      '<input type="range" class="servo-slider" min="0" max="180" value="90" id="pca_slider_' + idx + '">' +
      '<span class="servo-value" id="pca_val_' + idx + '">90</span>';
    pcaContainer.appendChild(row);
    row.querySelector('input').oninput = function() {
      var v = this.value;
      document.getElementById('pca_val_' + idx).textContent = v;
      fetch('/servo?ch=' + idx + '&angle=' + v);
    };
  })(i);
}

// STS3032 Servo controls
var stsContainer = document.getElementById('stsServoControls');
var stsLabels = ['左前腿', '右前腿', '左后腿', '右后腿'];
for (var id = 1; id <= 4; id++) {
  (function(sid) {
    var row = document.createElement('div');
    row.className = 'servo-row';
    row.innerHTML = '<span class="servo-label">' + stsLabels[sid-1] + '</span>' +
      '<input type="range" class="servo-slider" min="0" max="4095" value="2048" id="sts_slider_' + sid + '">' +
      '<span class="servo-value" id="sts_val_' + sid + '">2048</span>';
    stsContainer.appendChild(row);
    row.querySelector('input').oninput = function() {
      var v = this.value;
      document.getElementById('sts_val_' + sid).textContent = v;
      fetch('/sts?id=' + sid + '&pos=' + v);
    };
  })(id);
}

// Camera Settings controls
var camContainer = document.getElementById('camSettings');
var camParams = [
  {param: 'brightness', label: '🔆 亮度', min: -2, max: 2, defaultVal: 0, step: 1},
  {param: 'contrast', label: '🎨 对比度', min: -2, max: 2, defaultVal: 1, step: 1},
  {param: 'saturation', label: '🌈 饱和度', min: -2, max: 2, defaultVal: 0, step: 1},
  {param: 'sharpness', label: '🔍 锐度', min: -2, max: 2, defaultVal: 0, step: 1},
  {param: 'ae_level', label: '💡 曝光补偿', min: -2, max: 2, defaultVal: 0, step: 1}
];
camParams.forEach(function(cfg) {
  var row = document.createElement('div');
  row.className = 'servo-row';
  row.innerHTML = '<span class="servo-label">' + cfg.label + '</span>' +
    '<input type="range" class="servo-slider" min="' + cfg.min + '" max="' + cfg.max + '" value="' + cfg.defaultVal + '" step="' + cfg.step + '" id="cam_' + cfg.param + '">' +
    '<span class="servo-value" id="cam_val_' + cfg.param + '">' + cfg.defaultVal + '</span>';
  camContainer.appendChild(row);
  row.querySelector('input').oninput = function() {
    var v = this.value;
    document.getElementById('cam_val_' + cfg.param).textContent = v;
    setCamSetting(cfg.param, parseInt(v));
  };
});

function setCamSetting(param, value) {
  if (ws_ui && ws_ui.readyState === 1) {
    ws_ui.send('CAMSET:' + param + ',' + value);
  } else {
    fetch('/camera_setting?param=' + param + '&value=' + value);
  }
}

// TFT Screen Settings controls
var tftContainer = document.getElementById('tftSettings');
var tftParams = [
  {param: 'brightness', label: '🔆 亮度', min: 0, max: 255, defaultVal: 255, step: 5}
];
tftParams.forEach(function(cfg) {
  var row = document.createElement('div');
  row.className = 'servo-row';
  row.innerHTML = '<span class="servo-label">' + cfg.label + '</span>' +
    '<input type="range" class="servo-slider" min="' + cfg.min + '" max="' + cfg.max + '" value="' + cfg.defaultVal + '" step="' + cfg.step + '" id="tft_' + cfg.param + '">' +
    '<span class="servo-value" id="tft_val_' + cfg.param + '">' + cfg.defaultVal + '</span>';
  tftContainer.appendChild(row);
  row.querySelector('input').oninput = function() {
    var v = this.value;
    document.getElementById('tft_val_' + cfg.param).textContent = v;
    setTftSetting(cfg.param, parseInt(v));
  };
});

function setTftSetting(param, value) {
  if (ws_ui && ws_ui.readyState === 1) {
    ws_ui.send('TFTSET:' + param + ',' + value);
  } else {
    fetch('/tft_setting?param=' + param + '&value=' + value);
  }
}

// 动画名称表（与 data/anim1~anim8 对应）
var ANIM_NAMES = {1:'待机1',2:'待机2',3:'待机3',4:'哭',5:'生气',6:'开心',7:'难过',8:'害羞'};
var TOTAL_ANIMS = 8;

// 动画控制函数
function playAnimation(animNum) {
  if (animNum < 1 || animNum > TOTAL_ANIMS) return;
  
  var name = ANIM_NAMES[animNum] || ('Anim ' + animNum);
  document.getElementById('animStatus').textContent = '播放中: ' + name;
  
  for (var i = 1; i <= TOTAL_ANIMS; i++) {
    var btn = document.getElementById('anim-btn-' + i);
    if (btn) {
      if (i === animNum) {
        btn.style.background = 'linear-gradient(135deg, #00ff88, #00cc6a)';
        btn.style.transform = 'scale(1.05)';
      } else {
        btn.style.background = 'linear-gradient(135deg, #00d4ff, #0099cc)';
        btn.style.transform = 'scale(1)';
      }
    }
  }
  
  if (ws_ui && ws_ui.readyState === 1) {
    ws_ui.send('ANIM:' + animNum);
  } else {
    fetch('/anim?play=' + animNum).catch(function(err) {
      console.error('Failed to play animation:', err);
    });
  }
}

// 按概率随机选择动画编号
// 待机(1/2/3)共60%  开心+害羞(6/8)共30%  哭+生气+难过(4/5/7)共10%
function selectRandomAnimation() {
  var rand = Math.random() * 100;
  if (rand < 60) {
    // 待机组 60%：在1/2/3中随机
    var idle = [1, 2, 3];
    return idle[Math.floor(Math.random() * 3)];
  } else if (rand < 90) {
    // 开心+害羞组 30%：在6/8中随机
    return Math.random() < 0.5 ? 6 : 8;
  } else {
    // 哭+生气+难过组 10%：在4/5/7中随机
    var emo = [4, 5, 7];
    return emo[Math.floor(Math.random() * 3)];
  }
}

// 获取动画播放时长（毫秒）
function getAnimDuration(animNum) {
  if (animFrames[animNum]) {
    return animFrames[animNum] * FRAME_DELAY_MS;
  }
  return 3000; // 默认3秒（如果未知）
}

function scheduleNextAnimation(currentAnimNum) {
  if (!animLoopActive) return;
  
  // 获取当前动画的播放时长
  var currentDuration = getAnimDuration(currentAnimNum);
  
  // 在动画播放完成前50ms发送下一个动画指令，确保无缝衔接
  var delay = currentDuration - 50;
  if (delay < 0) delay = 0;
  
  console.log('[ANIM] Current anim ' + currentAnimNum + ' duration: ' + currentDuration + 'ms, scheduling next in ' + delay + 'ms');
  
  animLoopInterval = setTimeout(function() {
    if (animLoopActive) {
      var animNum = selectRandomAnimation();
      playAnimation(animNum);
      scheduleNextAnimation(animNum); // 递归调度下一次，传递新动画编号
    }
  }, delay);
}

// 从ESP32获取动画帧数信息
function loadAnimInfo() {
  fetch('/animlist')
    .then(function(res) { return res.json(); })
    .then(function(data) {
      animFrames = {};
      
      // 从ESP32获取实际的帧延迟
      if (data.frameDelayMs) {
        FRAME_DELAY_MS = data.frameDelayMs;
        console.log('[ANIM] Frame delay: ' + FRAME_DELAY_MS + 'ms');
      }
      
      if (data.anims && data.anims.length > 0) {
        data.anims.forEach(function(anim) {
          animFrames[anim.id] = anim.frames;
          var duration = anim.frames * FRAME_DELAY_MS;
          console.log('[ANIM] Loaded anim ' + anim.id + ': ' + anim.frames + ' frames (' + duration + 'ms = ' + (duration/1000).toFixed(2) + 's)');
        });
        console.log('[ANIM] Total animations loaded: ' + data.anims.length);
      }
    })
    .catch(function(err) {
      console.error('[ANIM] Failed to load animation info:', err);
    });
}

function playAnimationLoop() {
  if (animLoopActive) {
    console.log('Animation loop already running');
    return;
  }
  
  // 确保已加载动画信息
  if (Object.keys(animFrames).length === 0) {
    loadAnimInfo();
    setTimeout(function() {
      if (Object.keys(animFrames).length === 0) {
        alert('无法获取动画信息，请稍后重试');
        return;
      }
      playAnimationLoop();
    }, 500);
    return;
  }
  
  animLoopActive = true;
  document.getElementById('animStatus').textContent = '随机播放中...';
  
  for (var i = 1; i <= TOTAL_ANIMS; i++) {
    var btn = document.getElementById('anim-btn-' + i);
    if (btn) {
      btn.style.background = 'linear-gradient(135deg, #a855f7, #7c3aed)';
      btn.style.transform = 'scale(1)';
    }
  }
  
  // 立即播放一次
  var animNum = selectRandomAnimation();
  playAnimation(animNum);
  
  // 调度下一次播放
  scheduleNextAnimation(animNum);
}

function stopAnimation() {
  animLoopActive = false;
  
  if (animLoopInterval) {
    clearTimeout(animLoopInterval);
    animLoopInterval = null;
  }
  
  document.getElementById('animStatus').textContent = '已停止';
  
  for (var i = 1; i <= TOTAL_ANIMS; i++) {
    var btn = document.getElementById('anim-btn-' + i);
    if (btn) {
      btn.style.background = 'linear-gradient(135deg, #00d4ff, #0099cc)';
      btn.style.transform = 'scale(1)';
    }
  }
  
  // 发送停止命令到ESP32
  if (ws_ui && ws_ui.readyState === 1) {
    ws_ui.send('ANIM:STOP');
  } else {
    fetch('/anim?stop=1').catch(function(err) {
      console.error('Failed to stop animation:', err);
    });
  }
}

function sendGait(mode) {
  if (ws_ui && ws_ui.readyState === 1) {
    ws_ui.send('GAIT:' + mode);
    updateGaitDisplay(mode);
  }
}

// 充电控制
function sendCharge(on) {
  var angle = on ? 0 : 90;  // 充电=尾巴转到0度, 恢复=回到90度
  var cmd = 'SERVO:13,' + angle;
  if (ws_ui && ws_ui.readyState === 1) {
    ws_ui.send(cmd);
  } else {
    fetch('/servo?ch=13&angle=' + angle);
  }
  // 更新状态显示
  var statusEl = document.getElementById('chargeStatus');
  if (statusEl) {
    statusEl.textContent = on ? '🔌 充电中...' : '🔋 未充电';
    statusEl.style.color = on ? '#00ff88' : '#00d4ff';
  }
  // 同步尾巴滑条
  var slider = document.getElementById('pca_slider_13');
  var valEl = document.getElementById('pca_val_13');
  if (slider) slider.value = angle;
  if (valEl) valEl.textContent = angle;
}

function updateGaitDisplay(mode) {
  if (mode !== 'LEFT' && mode !== 'RIGHT' && mode !== 'STRAIGHT') {
    currentGait = mode;
    document.getElementById('gaitMode').textContent = mode;
  }
  if (mode === 'LEFT') {
    document.getElementById('turnDir').textContent = '← 左转';
  } else if (mode === 'RIGHT') {
    document.getElementById('turnDir').textContent = '右转 →';
  } else if (mode === 'STRAIGHT') {
    document.getElementById('turnDir').textContent = '直行';
  }
}

function connectCamera() {
  if (ws_viewer) ws_viewer.close();
  var host = location.host || 'localhost:8081';
  ws_viewer = new WebSocket('ws://' + host + '/ws/viewer');
  ws_viewer.binaryType = 'arraybuffer';
  
  ws_viewer.onopen = function() {
    camStatus.textContent = 'Connected';
    camStatus.className = 'status online';
  };
  
  ws_viewer.onclose = function() {
    camStatus.textContent = 'Disconnected';
    camStatus.className = 'status offline';
  };
  
  ws_viewer.onmessage = function(e) {
    if (e.data instanceof ArrayBuffer) {
      var blob = new Blob([e.data], {type: 'image/jpeg'});
      var url = URL.createObjectURL(blob);
      var img = new Image();
      img.onload = function() {
        // 清空画布
        ctx.clearRect(0, 0, canvas.width, canvas.height);
        // 直接绘制（画面方向由 ESP32 端的传感器翻转控制）
        ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
        URL.revokeObjectURL(url);
      };
      img.src = url;
    }
  };
}

function disconnectCamera() {
  if (ws_viewer) ws_viewer.close();
}

function connectUI() {
  if (ws_ui) ws_ui.close();
  var host = location.host || 'localhost:8081';
  ws_ui = new WebSocket('ws://' + host + '/ws_ui');
  
  ws_ui.onopen = function() {
    uiStatus.textContent = 'Connected';
    uiStatus.className = 'status online';
  };
  
  ws_ui.onclose = function() {
    uiStatus.textContent = 'Disconnected';
    uiStatus.className = 'status offline';
  };
  
  ws_ui.onmessage = function(e) {
    var data = e.data;
    if (data.indexOf('PARTIAL:') === 0) {
      partialText.textContent = data.substring(8) || '...';
    } else if (data.indexOf('FINAL:') === 0) {
      var text = data.substring(6);
      if (text.indexOf('[AI]') === 0 || text.indexOf('[复述]') === 0) {
        addMessage(text, 'ai');
      } else {
        addMessage(text, 'user');
      }
      partialText.textContent = '';
    } else if (data.indexOf('INIT:') === 0) {
      try {
        var init = JSON.parse(data.substring(5));
        for (var i = 0; i < init.finals.length; i++) {
          var f = init.finals[i];
          addMessage(f, f.indexOf('[AI]') === 0 || f.indexOf('[复述]') === 0 ? 'ai' : 'user');
        }
        if (init.partial) partialText.textContent = init.partial;
      } catch(err) {}
    }
  };
}

function disconnectUI() {
  if (ws_ui) ws_ui.close();
}

function addMessage(text, type) {
  var div = document.createElement('div');
  div.className = type === 'ai' ? 'chat-ai' : 'chat-final';
  div.textContent = text;
  chatBox.insertBefore(div, partialText);
  chatBox.scrollTop = chatBox.scrollHeight;
}

// 语音复述功能
function sendRepeat() {
  var text = document.getElementById('repeatText').value.trim();
  if (!text) {
    alert('请先输入要复述的文字！');
    return;
  }
  if (!ws_ui || ws_ui.readyState !== 1) {
    alert('请先连接UI WebSocket！');
    return;
  }
  
  console.log('[REPEAT] Sending text:', text);
  ws_ui.send('REPEAT:' + text);
  
  // 显示提示
  addMessage('[复述] 开始播放: ' + text, 'user');
}

function clearRepeatText() {
  document.getElementById('repeatText').value = '';
}

// 键盘控制
function sendKeyEvent(eventType, key, shift) {
  if (ws_ui && ws_ui.readyState === 1) {
    var msg = 'KEY:' + eventType + ':' + key;
    if (shift) msg += ':SHIFT';
    ws_ui.send(msg);
  }
}

function highlightKey(keyId, active) {
  var el = document.getElementById(keyId);
  if (el) {
    if (active) {
      el.classList.add('active-key');
    } else {
      el.classList.remove('active-key');
    }
  }
}

function updateActiveKeysDisplay() {
  var keys = Array.from(pressedKeys);
  document.getElementById('activeKeys').textContent = keys.length > 0 ? keys.join('+') : '-';
}

document.addEventListener('keydown', function(e) {
  // 防止重复触发
  if (e.repeat) return;
  
  var key = e.key.toUpperCase();
  if (key === ' ') key = 'SPACE';
  if (key === 'ESCAPE') key = 'ESC';
  
  pressedKeys.add(key);
  updateActiveKeysDisplay();
  
  // 高亮按键
  var keyMap = {
    'W': 'key-w', 'A': 'key-a', 'S': 'key-s', 'D': 'key-d', 'E': 'key-e',
    'R': 'key-r', 'T': 'key-t', 'B': 'key-b', 'V': 'key-v', 'L': 'key-l',
    'J': 'key-j', 'C': 'key-c', 'SPACE': 'key-space', 'ESC': 'key-esc'
  };
  if (keyMap[key]) highlightKey(keyMap[key], true);
  
  // 发送按键事件
  if (['W', 'A', 'S', 'D', 'E', 'R', 'T', 'B', 'V', 'L', 'J', 'C', 'SPACE', 'ESC'].indexOf(key) >= 0) {
    sendKeyEvent('DOWN', key, e.shiftKey);
    e.preventDefault();
    
    // 更新显示
    if (key === 'W') {
      updateGaitDisplay(e.shiftKey ? 'TROT' : 'WALK');
    } else if (key === 'T') {
      updateGaitDisplay('TROT_STRAIGHT');
    } else if (key === 'E') {
      updateGaitDisplay('EFFICIENT_WALK');
    } else if (key === 'R') {
      updateGaitDisplay('RUN');
    } else if (key === 'B') {
      updateGaitDisplay('BACKWARD');
    } else if (key === 'V') {
      updateGaitDisplay('WAVE');
    } else if (key === 'A') {
      updateGaitDisplay('LEFT');
    } else if (key === 'D') {
      updateGaitDisplay('RIGHT');
    } else if (key === 'S') {
      updateGaitDisplay('SIT');
    } else if (key === 'L') {
      updateGaitDisplay('LAYDOWN');
    } else if (key === 'J') {
      updateGaitDisplay('JUMP');
    } else if (key === 'SPACE') {
      updateGaitDisplay('IDLE');
    } else if (key === 'ESC') {
      updateGaitDisplay('STOP');
    } else if (key === 'C') {
      updateGaitDisplay('STOP');
    }
  }
});

document.addEventListener('keyup', function(e) {
  var key = e.key.toUpperCase();
  if (key === ' ') key = 'SPACE';
  if (key === 'ESCAPE') key = 'ESC';
  
  pressedKeys.delete(key);
  updateActiveKeysDisplay();
  
  // 取消高亮
  var keyMap = {
    'W': 'key-w', 'A': 'key-a', 'S': 'key-s', 'D': 'key-d', 'E': 'key-e',
    'R': 'key-r', 'T': 'key-t', 'B': 'key-b', 'V': 'key-v', 'L': 'key-l',
    'J': 'key-j', 'C': 'key-c', 'SPACE': 'key-space', 'ESC': 'key-esc'
  };
  if (keyMap[key]) highlightKey(keyMap[key], false);
  
  // 发送按键释放事件
  if (['W', 'A', 'S', 'D', 'E', 'R', 'T', 'B', 'V', 'L', 'J', 'C', 'SPACE', 'ESC'].indexOf(key) >= 0) {
    sendKeyEvent('UP', key, e.shiftKey);
    
    // 更新显示
    if (key === 'A' || key === 'D') {
      updateGaitDisplay('STRAIGHT');
    } else if (key === 'W' || key === 'R' || key === 'T' || key === 'B' || key === 'E') {
      updateGaitDisplay('STOP');
    }
  }
});

// Auto connect on load
setTimeout(function() {
  connectCamera();
  connectUI();
  loadAnimInfo(); // 加载动画信息
}, 500);
</script>
</body>
</html>"""


if __name__ == "__main__":
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8081,
        log_level="info",
        access_log=False
    )
