# asr_core.py
# -*- coding: utf-8 -*-
import os, json, asyncio
from typing import Any, Dict, List, Optional, Callable, Tuple

ASR_DEBUG_RAW = os.getenv("ASR_DEBUG_RAW", "0") == "1"

def _shorten(s: str, limit: int = 200) -> str:
    if not s:
        return ""
    return s if len(s) <= limit else (s[:limit] + "…")

def _safe_to_dict(x: Any) -> Dict[str, Any]:
    if isinstance(x, dict): return x
    for attr in ("to_dict", "model_dump", "__dict__"):
        try:
            v = getattr(x, attr, None)
        except Exception:
            v = None
        if callable(v):
            try:
                d = v()
                if isinstance(d, dict): return d
            except Exception:
                pass
        elif isinstance(v, dict):
            return v
    try:
        s = str(x)
        if s and s.lstrip().startswith("{") and s.rstrip().endswith("}"):
            return json.loads(s)
    except Exception:
        pass
    return {"_raw": str(x)}

def _extract_sentence(event_obj: Any) -> Tuple[Optional[str], Optional[bool]]:
    d = _safe_to_dict(event_obj)
    cands: List[Dict[str, Any]] = [d]
    for k in ("output", "data", "result"):
        v = d.get(k)
        if isinstance(v, dict):
            cands.append(v)
    for obj in cands:
        sent = obj.get("sentence")
        if isinstance(sent, dict):
            text = sent.get("text")
            is_end = sent.get("sentence_end")
            if is_end is not None:
                is_end = bool(is_end)
            return text, is_end
    for obj in cands:
        if "text" in obj and isinstance(obj.get("text"), str):
            return obj.get("text"), None
    return None, None

# ====== 仅热词触发的"全清零复位"配置 ======
INTERRUPT_KEYWORDS = set(
    os.getenv("INTERRUPT_KEYWORDS", "停下,别说了,停止").split(",")
)

def _normalize_cn(s: str) -> str:
    try:
        import unicodedata
        s = "".join(" " if unicodedata.category(ch) == "Zs" else ch for ch in s)
        s = s.strip().lower()
    except Exception:
        s = (s or "").strip().lower()
    return s

# ============ ASR 全局总闸 ============
_current_recognition: Optional[object] = None
_rec_lock = asyncio.Lock()

async def set_current_recognition(r):
    global _current_recognition
    async with _rec_lock:
        _current_recognition = r

async def stop_current_recognition():
    global _current_recognition
    async with _rec_lock:
        r = _current_recognition
        _current_recognition = None
    if r:
        try:
            r.stop()  # DashScope SDK 的实时识别停止
        except Exception:
            pass

# ============ ASR 回调 ============
class ASRCallback:
    """
    设计目标：
    1) "停下 / 别说了 …"等热词一出现 → 立刻全清零复位（恢复到刚启动后的状态）。
    2) 除此之外【不接受打断】；AI 正在播报时，用户说话只做展示，不触发新一轮。
    3) 不再用 partial 叠加字符串；partial 只用于 UI 临时展示；只有 final sentence 用于驱动 AI。
    """

    def __init__(
        self,
        on_sdk_error: Callable[[str], None],
        post: Callable[[asyncio.Future], None],
        ui_broadcast_partial,
        ui_broadcast_final,
        is_playing_now_fn: Callable[[], bool],
        start_ai_with_text_fn,               # async (text)
        full_system_reset_fn,                 # async (reason)
        interrupt_lock: asyncio.Lock,
    ):
        self._on_sdk_error = on_sdk_error
        self._post = post
        self._last_partial_for_ui: str = ""   # 只用于 UI 展示
        self._last_final_text: str = ""       # 以句末 final 为准
        self._hot_interrupted: bool = False   # 本句是否因热词触发过复位（防抖）

        self._ui_partial = ui_broadcast_partial
        self._ui_final   = ui_broadcast_final
        self._is_playing = is_playing_now_fn
        self._start_ai   = start_ai_with_text_fn
        self._full_reset = full_system_reset_fn
        self._interrupt_lock = interrupt_lock
        
        # 冷却期：ASR会话刚启动时忽略识别结果（防止识别到AI残留音频）
        import time
        self._session_start_time = time.time()
        self._cooldown_seconds = 3.0  # 前3秒内忽略所有识别（除了热词），配合ESP32端500ms麦克风延迟

    def on_open(self):  pass
    def on_close(self): pass
    def on_complete(self): pass

    def reset_state(self):
        """清空所有内部状态，防止旧识别结果残留"""
        import time
        self._last_partial_for_ui = ""
        self._last_final_text = ""
        self._hot_interrupted = False
        self._session_start_time = time.time()  # 重置冷却期计时器
        print("[ASR] 回调状态已重置，冷却期重新开始", flush=True)

    def on_error(self, err):
        try:
            self._post(self._ui_partial(""))
            self._on_sdk_error(str(err))
        except Exception:
            pass

    def on_result(self, result): self._handle(result)
    def on_event(self,  event):  self._handle(event)

    def _has_hotword(self, text: str) -> bool:
        t = _normalize_cn(text)
        if not t: return False
        for w in INTERRUPT_KEYWORDS:
            if w and _normalize_cn(w) in t:
                return True
        return False

    def _handle(self, event: Any):
        if ASR_DEBUG_RAW:
            try:
                rawd = _safe_to_dict(event)
                print("[ASR EVENT RAW]", json.dumps(rawd, ensure_ascii=False), flush=True)
            except Exception:
                pass

        text, is_end = _extract_sentence(event)
        if text is None:
            return
        text = text.strip()
        if not text:
            return

        # ---------- ① 热词优先：命中就全清零并短路，绝不送 LLM ----------
        if not self._hot_interrupted and self._has_hotword(text):
            self._hot_interrupted = True

            async def _hot_reset():
                async with self._interrupt_lock:
                    print(f"[ASR HOTWORD] '{text}' -> FULL RESET, skip LLM", flush=True)
                    await self._full_reset("Hotword interrupt")
            try:
                self._post(_hot_reset())
            except Exception:
                pass
            return

        # ---------- ② AI播放时完全忽略所有ASR结果（关键！） ----------
        if self._is_playing():
            # AI正在说话，直接丢弃所有识别结果（partial和final都不处理）
            # 这样前端不会显示任何文字，也不会触发LLM
            print(f"[ASR IGNORED] AI正在播放，忽略识别结果: '{_shorten(text)}'", flush=True)
            return
        
        # ---------- ③ 冷却期：ASR会话刚启动时忽略识别结果（防止识别到AI残留音频） ----------
        import time
        elapsed = time.time() - self._session_start_time
        if elapsed < self._cooldown_seconds:
            print(f"[ASR COOLDOWN] 冷却期内({elapsed:.1f}s < {self._cooldown_seconds}s)，忽略识别结果: '{_shorten(text)}'", flush=True)
            return

        # ---------- ④ partial：仅用于 UI 展示 ----------
        self._last_partial_for_ui = text
        try:
            print(f"[ASR PARTIAL] len={len(text)} text='{_shorten(text)}'", flush=True)
            self._post(self._ui_partial(self._last_partial_for_ui))
        except Exception:
            pass

        # ---------- ⑤ final：仅 final 驱动 LLM（若未在播报） ----------
        if is_end is True:
            final_text = text
            try:
                print(f"[ASR FINAL]  len={len(final_text)} text='{final_text}'", flush=True)
                self._post(self._ui_final(final_text))
            except Exception:
                pass

            if (not self._is_playing()) and final_text:
                async def _run_final():
                    async with self._interrupt_lock:
                        print(f"[LLM INPUT TEXT] {final_text}", flush=True)
                        await self._start_ai(final_text)
                try:
                    self._post(_run_final())
                except Exception:
                    pass

            # 复位进入下一句
            self._last_partial_for_ui = ""
            self._last_final_text = ""
            self._hot_interrupted = False











