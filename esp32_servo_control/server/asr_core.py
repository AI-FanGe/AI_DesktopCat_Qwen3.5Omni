# -*- coding: utf-8 -*-
import asyncio
import json
import os
from typing import Any, Callable, Dict, List, Optional, Tuple

ASR_DEBUG_RAW = os.getenv("ASR_DEBUG_RAW", "0") == "1"


def _shorten(text: str, limit: int = 200) -> str:
    return text if len(text) <= limit else text[:limit] + "…"


def _safe_to_dict(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return value
    for attr in ("to_dict", "model_dump", "__dict__"):
        try:
            maybe = getattr(value, attr, None)
        except Exception:
            maybe = None
        if callable(maybe):
            try:
                data = maybe()
                if isinstance(data, dict):
                    return data
            except Exception:
                pass
        elif isinstance(maybe, dict):
            return maybe
    try:
        raw = str(value)
        if raw and raw.lstrip().startswith("{") and raw.rstrip().endswith("}"):
            return json.loads(raw)
    except Exception:
        pass
    return {"_raw": str(value)}


def _extract_sentence(event_obj: Any) -> Tuple[Optional[str], Optional[bool]]:
    data = _safe_to_dict(event_obj)
    candidates: List[Dict[str, Any]] = [data]
    for key in ("output", "data", "result"):
        value = data.get(key)
        if isinstance(value, dict):
            candidates.append(value)
    for obj in candidates:
        sentence = obj.get("sentence")
        if isinstance(sentence, dict):
            text = sentence.get("text")
            sentence_end = sentence.get("sentence_end")
            if sentence_end is not None:
                sentence_end = bool(sentence_end)
            return text, sentence_end
    for obj in candidates:
        if isinstance(obj.get("text"), str):
            return obj.get("text"), None
    return None, None


INTERRUPT_KEYWORDS = set(os.getenv("INTERRUPT_KEYWORDS", "停下,别说了,停止,闭嘴").split(","))

_current_recognition: Optional[object] = None
_rec_lock = asyncio.Lock()


async def set_current_recognition(recognition):
    global _current_recognition
    async with _rec_lock:
        _current_recognition = recognition


async def stop_current_recognition():
    global _current_recognition
    async with _rec_lock:
        recognition = _current_recognition
        _current_recognition = None
    if recognition:
        try:
            recognition.stop()
        except Exception:
            pass


class ASRCallback:
    def __init__(
        self,
        on_sdk_error: Callable[[str], None],
        post: Callable[[asyncio.Future], None],
        ui_broadcast_partial,
        ui_broadcast_final,
        is_playing_now_fn: Callable[[], bool],
        start_ai_with_text_fn,
        full_system_reset_fn,
        interrupt_lock: asyncio.Lock,
    ):
        self._on_sdk_error = on_sdk_error
        self._post = post
        self._ui_partial = ui_broadcast_partial
        self._ui_final = ui_broadcast_final
        self._is_playing = is_playing_now_fn
        self._start_ai = start_ai_with_text_fn
        self._full_reset = full_system_reset_fn
        self._interrupt_lock = interrupt_lock
        self._hot_interrupted = False
        self._last_partial = ""
        import time

        self._session_start_time = time.time()
        self._cooldown_seconds = 3.0

    def on_open(self):
        pass

    def on_close(self):
        pass

    def on_complete(self):
        pass

    def on_error(self, err):
        try:
            self._post(self._ui_partial(""))
            self._on_sdk_error(str(err))
        except Exception:
            pass

    def on_result(self, result):
        self._handle(result)

    def on_event(self, event):
        self._handle(event)

    def _has_hotword(self, text: str) -> bool:
        normalized = (text or "").strip().lower()
        return any(keyword and keyword.strip().lower() in normalized for keyword in INTERRUPT_KEYWORDS)

    def _handle(self, event: Any):
        if ASR_DEBUG_RAW:
            try:
                print("[ASR EVENT RAW]", json.dumps(_safe_to_dict(event), ensure_ascii=False), flush=True)
            except Exception:
                pass

        text, is_end = _extract_sentence(event)
        if text is None:
            return
        text = text.strip()
        if not text:
            return

        if not self._hot_interrupted and self._has_hotword(text):
            self._hot_interrupted = True

            async def _reset():
                async with self._interrupt_lock:
                    print(f"[ASR HOTWORD] {text}", flush=True)
                    await self._full_reset("hotword interrupt")

            self._post(_reset())
            return

        if self._is_playing():
            print(f"[ASR IGNORED] { _shorten(text) }", flush=True)
            return

        import time

        elapsed = time.time() - self._session_start_time
        if elapsed < self._cooldown_seconds:
            return

        self._last_partial = text
        self._post(self._ui_partial(text))

        if is_end is True:
            final_text = text
            self._post(self._ui_final(final_text))
            if final_text and not self._is_playing():

                async def _run():
                    async with self._interrupt_lock:
                        await self._start_ai(final_text)

                self._post(_run())
            self._last_partial = ""
            self._hot_interrupted = False
