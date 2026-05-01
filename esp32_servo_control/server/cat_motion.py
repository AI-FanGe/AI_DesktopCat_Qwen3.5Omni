# -*- coding: utf-8 -*-
import random
from typing import Dict, List, Optional

HEAD_YAW_CENTER = 90
HEAD_PITCH_CENTER = 90
EAR_CENTER = 90


def _clamp_angle(value: int) -> int:
    return max(0, min(180, int(value)))


EMOTION_PROFILES: Dict[str, Dict[str, object]] = {
    "happy": {
        "frames": (3, 5),
        "yaw": (72, 108),
        "pitch": (78, 95),
        "ear": (45, 70),
        "duration": (180, 280),
    },
    "sad": {
        "frames": (2, 4),
        "yaw": (82, 98),
        "pitch": (102, 122),
        "ear": (108, 138),
        "duration": (320, 520),
    },
    "angry": {
        "frames": (3, 5),
        "yaw": (60, 120),
        "pitch": (72, 88),
        "ear": (118, 150),
        "duration": (160, 260),
    },
    "surprised": {
        "frames": (2, 3),
        "yaw": (75, 105),
        "pitch": (62, 78),
        "ear": (28, 48),
        "duration": (160, 240),
    },
    "thinking": {
        "frames": (2, 4),
        "yaw": (58, 122),
        "pitch": (80, 96),
        "ear": (70, 108),
        "duration": (240, 380),
    },
    "sleepy": {
        "frames": (2, 3),
        "yaw": (84, 96),
        "pitch": (108, 128),
        "ear": (112, 145),
        "duration": (420, 700),
    },
    "excited": {
        "frames": (4, 6),
        "yaw": (52, 128),
        "pitch": (72, 92),
        "ear": (30, 58),
        "duration": (140, 220),
    },
    "confused": {
        "frames": (2, 4),
        "yaw": (58, 122),
        "pitch": (86, 110),
        "ear": (65, 115),
        "duration": (220, 360),
    },
    "love": {
        "frames": (3, 5),
        "yaw": (76, 104),
        "pitch": (86, 102),
        "ear": (52, 82),
        "duration": (220, 340),
    },
    "fear": {
        "frames": (3, 5),
        "yaw": (66, 114),
        "pitch": (100, 118),
        "ear": (125, 156),
        "duration": (140, 220),
    },
    "shy": {
        "frames": (2, 4),
        "yaw": (74, 112),
        "pitch": (94, 116),
        "ear": (92, 126),
        "duration": (220, 360),
    },
    "neutral": {
        "frames": (1, 2),
        "yaw": (84, 96),
        "pitch": (86, 96),
        "ear": (82, 98),
        "duration": (300, 480),
    },
    "listening": {
        "frames": (1, 2),
        "yaw": (82, 98),
        "pitch": (82, 92),
        "ear": (34, 54),
        "duration": (340, 560),
    },
}

DEFAULT_PROFILE = EMOTION_PROFILES["neutral"]


def generate_cat_emotion_actions(emotion: str) -> List[Dict[str, int]]:
    profile = EMOTION_PROFILES.get((emotion or "").lower(), DEFAULT_PROFILE)
    frame_total = random.randint(*profile["frames"])  # type: ignore[arg-type]
    frames: List[Dict[str, int]] = []

    for _ in range(frame_total):
        yaw = random.randint(*profile["yaw"])  # type: ignore[arg-type]
        pitch = random.randint(*profile["pitch"])  # type: ignore[arg-type]
        ear = random.randint(*profile["ear"])  # type: ignore[arg-type]
        duration = random.randint(*profile["duration"])  # type: ignore[arg-type]
        frames.append(
            {
                "yaw": _clamp_angle(yaw),
                "pitch": _clamp_angle(pitch),
                "ear": _clamp_angle(ear),
                "duration": max(80, duration),
            }
        )

    frames.append(
        {
            "yaw": HEAD_YAW_CENTER,
            "pitch": HEAD_PITCH_CENTER,
            "ear": EAR_CENTER,
            "duration": 260,
        }
    )
    return frames


def estimate_cat_emotion_duration_ms(actions: List[Dict[str, int]]) -> int:
    total = 0
    for frame in actions:
        total += max(80, int(frame.get("duration", 0)))
    return total


def format_catpose_command(frame: Dict[str, int]) -> str:
    return f"CATPOSE:{frame['yaw']},{frame['pitch']},{frame['ear']},{frame['duration']}"


async def send_cat_emotion_actions(ws, emotion: str, clear_first: bool = True, ear_override: Optional[int] = None):
    from starlette.websockets import WebSocketState

    if not ws or ws.client_state != WebSocketState.CONNECTED:
        return 0

    actions = generate_cat_emotion_actions(emotion)
    total_duration_ms = estimate_cat_emotion_duration_ms(actions)
    if clear_first:
        await ws.send_text("CATPOSE:CLEAR")

    for frame in actions:
        if ear_override is not None:
            frame["ear"] = _clamp_angle(ear_override)
        await ws.send_text(format_catpose_command(frame))
    return total_duration_ms
