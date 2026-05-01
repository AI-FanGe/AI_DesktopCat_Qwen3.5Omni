# -*- coding: utf-8 -*-
import asyncio
import base64
import inspect
import json
import os
import uuid
from typing import Awaitable, Callable, Optional

import websockets
from websockets.exceptions import ConnectionClosed

REALTIME_MODEL = os.getenv("QWEN_REALTIME_MODEL", "qwen3.5-omni-plus-realtime")
REALTIME_VOICE = os.getenv("QWEN_REALTIME_VOICE", "Sunnybobi")
REALTIME_TURN_DETECTION = os.getenv("QWEN_REALTIME_TURN_DETECTION", "server_vad")
REALTIME_TURN_THRESHOLD = float(os.getenv("QWEN_REALTIME_TURN_THRESHOLD", "0.5"))
REALTIME_TURN_SILENCE_MS = int(os.getenv("QWEN_REALTIME_TURN_SILENCE_MS", "900"))
REALTIME_PREFIX_PADDING_MS = int(os.getenv("QWEN_REALTIME_PREFIX_PADDING_MS", "500"))
REALTIME_INPUT_TRANSCRIPTION_MODEL = os.getenv("QWEN_REALTIME_TRANSCRIPTION_MODEL", "gummy-realtime-v1")
REALTIME_WS_URL = os.getenv("QWEN_REALTIME_WS_URL", "wss://dashscope.aliyuncs.com/api-ws/v1/realtime")
REALTIME_READY_TIMEOUT_S = float(os.getenv("QWEN_REALTIME_READY_TIMEOUT", "10"))
API_KEY = os.getenv("DASHSCOPE_API_KEY", "")


class OmniRealtimeSession:
    def __init__(
        self,
        *,
        loop: asyncio.AbstractEventLoop,
        system_prompt: str,
        on_input_transcript: Callable[[str], Awaitable[None]],
        on_output_text_delta: Callable[[str], Awaitable[None]],
        on_output_audio: Callable[[bytes], Awaitable[None]],
        on_response_started: Callable[[], Awaitable[None]],
        on_response_done: Callable[[], Awaitable[None]],
        on_speech_started: Callable[[], Awaitable[None]],
        on_error: Callable[[str], Awaitable[None]],
        on_debug: Optional[Callable[[str], Awaitable[None]]] = None,
        get_latest_image_bytes: Optional[Callable[[], bytes]] = None,
    ):
        self.loop = loop
        self.system_prompt = system_prompt
        self.on_input_transcript = on_input_transcript
        self.on_output_text_delta = on_output_text_delta
        self.on_output_audio = on_output_audio
        self.on_response_started = on_response_started
        self.on_response_done = on_response_done
        self.on_speech_started = on_speech_started
        self.on_error = on_error
        self.on_debug = on_debug
        self.get_latest_image_bytes = get_latest_image_bytes

        self._ws = None
        self._message_task: Optional[asyncio.Task] = None
        self._connect_lock = asyncio.Lock()
        self._send_lock = asyncio.Lock()
        self._connected = False
        self._responding = False
        self._image_sent_for_turn = False
        self._ready_event = asyncio.Event()
        self._last_error_message = ""
        self._last_close_message = ""
        self._session_updated = False
        self._fatal_error = False
        self._audio_appended_for_turn = False
        self._audio_append_count_for_turn = 0
        self._audio_append_count_after_speech_started = 0
        self._speech_started_for_turn = False
        self._pending_image_task: Optional[asyncio.Task] = None

    @property
    def is_fatal(self) -> bool:
        return self._fatal_error

    def _ws_is_closed(self, ws) -> bool:
        if ws is None:
            return True
        closed = getattr(ws, "closed", None)
        if isinstance(closed, bool):
            return closed
        state = getattr(ws, "state", None)
        if state is None:
            return False
        state_name = getattr(state, "name", str(state)).upper()
        return state_name in {"CLOSED", "CLOSING"}

    async def ensure_connected(self):
        if not API_KEY:
            raise RuntimeError("未设置 DASHSCOPE_API_KEY")

        async with self._connect_lock:
            if self._fatal_error:
                detail = self._last_error_message or self._last_close_message or "fatal realtime error"
                raise RuntimeError(detail)
            if self._ws is not None and self._connected and not self._ws_is_closed(self._ws):
                return

            self._ready_event.clear()
            self._last_error_message = ""
            self._last_close_message = ""
            self._session_updated = False

            url = f"{REALTIME_WS_URL}?model={REALTIME_MODEL}"
            headers = {"Authorization": f"Bearer {API_KEY}"}
            await self._debug(f"connecting url={REALTIME_WS_URL} model={REALTIME_MODEL}")
            connect_kwargs = {
                "open_timeout": 10,
                "max_size": None,
                "ping_interval": 20,
                "ping_timeout": 20,
            }
            sig = inspect.signature(websockets.connect)
            if "additional_headers" in sig.parameters:
                connect_kwargs["additional_headers"] = headers
            elif "extra_headers" in sig.parameters:
                connect_kwargs["extra_headers"] = headers
            else:
                connect_kwargs["extra_headers"] = headers
            try:
                self._ws = await websockets.connect(url, **connect_kwargs)
            except TypeError as exc:
                # 兼容某些环境下 websockets / loop 实现对 headers 参数名的差异
                msg = str(exc)
                if "additional_headers" in msg and "additional_headers" in connect_kwargs:
                    connect_kwargs["extra_headers"] = connect_kwargs.pop("additional_headers")
                    self._ws = await websockets.connect(url, **connect_kwargs)
                elif "extra_headers" in msg and "extra_headers" in connect_kwargs:
                    connect_kwargs["additional_headers"] = connect_kwargs.pop("extra_headers")
                    self._ws = await websockets.connect(url, **connect_kwargs)
                else:
                    raise
            self._message_task = asyncio.create_task(self._handle_messages())
            await self._update_session()
            await asyncio.wait_for(self._ready_event.wait(), timeout=REALTIME_READY_TIMEOUT_S)
            if not self._connected or not self._session_updated:
                detail = self._last_error_message or self._last_close_message or "服务端未返回 session.updated"
                await self.close()
                raise RuntimeError(f"Realtime 会话未就绪：{detail}")
            await self._debug("session ready")

    async def _send_event(self, event: dict):
        if self._ws is None or self._ws_is_closed(self._ws):
            self._connected = False
            raise RuntimeError(self._build_closed_error("WebSocket 未连接"))
        event["event_id"] = "event_" + uuid.uuid4().hex
        try:
            async with self._send_lock:
                await self._ws.send(json.dumps(event, ensure_ascii=False))
        except ConnectionClosed as exc:
            self._connected = False
            self._last_close_message = f"code={exc.code}, reason={exc.reason}"
            raise RuntimeError(self._build_closed_error("WebSocket 已关闭"))

    async def _update_session(self):
        session_config = {
            "modalities": ["text", "audio"],
            "voice": REALTIME_VOICE,
            "instructions": self.system_prompt,
            "input_audio_format": "pcm",
            "output_audio_format": "pcm",
            "input_audio_transcription": {
                "model": REALTIME_INPUT_TRANSCRIPTION_MODEL,
            },
            "turn_detection": {
                "type": REALTIME_TURN_DETECTION,
                "threshold": REALTIME_TURN_THRESHOLD,
                "prefix_padding_ms": REALTIME_PREFIX_PADDING_MS,
                "silence_duration_ms": REALTIME_TURN_SILENCE_MS,
            },
        }
        await self._debug(
            f"session.update turn_detection={REALTIME_TURN_DETECTION} threshold={REALTIME_TURN_THRESHOLD} silence_ms={REALTIME_TURN_SILENCE_MS}"
        )
        await self._send_event({"type": "session.update", "session": session_config})

    async def update_instructions(self, new_instructions: str, new_voice: Optional[str] = None):
        """Push a live session.update with a new system prompt (and optionally a new voice).

        Safe to call before the websocket is ready: we update the cached prompt
        so the next connection uses it.
        """
        self.system_prompt = new_instructions
        ws = self._ws
        if ws is None or self._ws_is_closed(ws) or not self._connected:
            await self._debug("update_instructions cached (session not ready)")
            return
        session_payload = {"instructions": new_instructions}
        if new_voice:
            session_payload["voice"] = new_voice
        try:
            await self._send_event({"type": "session.update", "session": session_payload})
            await self._debug("session.update pushed (instructions refreshed)")
        except Exception as exc:
            await self._debug(f"update_instructions failed: {exc}")

    async def append_audio(self, pcm_chunk: bytes):
        if not pcm_chunk:
            return
        await self.ensure_connected()
        payload = base64.b64encode(pcm_chunk).decode("ascii")
        await self._send_event({"type": "input_audio_buffer.append", "audio": payload})
        self._audio_appended_for_turn = True
        self._audio_append_count_for_turn += 1
        if self._speech_started_for_turn:
            self._audio_append_count_after_speech_started += 1
        if self._speech_started_for_turn and not self._image_sent_for_turn:
            self._schedule_image_append()

    async def send_text_turn(self, text: str):
        text = (text or "").strip()
        if not text:
            return
        await self.ensure_connected()
        self._image_sent_for_turn = False
        self._audio_appended_for_turn = False
        self._audio_append_count_for_turn = 0
        self._audio_append_count_after_speech_started = 0
        self._speech_started_for_turn = False
        await self._send_event(
            {
                "type": "conversation.item.create",
                "item": {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": text}],
                },
            }
        )
        await self._send_event(
            {
                "type": "response.create",
                "response": {
                    "modalities": ["text", "audio"],
                },
            }
        )

    async def cancel_response(self):
        if not self._responding:
            return
        await self._send_event({"type": "response.cancel"})

    async def close(self):
        ws = self._ws
        self._ws = None
        self._connected = False
        self._responding = False
        self._image_sent_for_turn = False
        self._audio_appended_for_turn = False
        self._audio_append_count_for_turn = 0
        self._audio_append_count_after_speech_started = 0
        self._speech_started_for_turn = False
        self._session_updated = False
        pending_task = self._pending_image_task
        self._pending_image_task = None
        task = self._message_task
        self._message_task = None
        if pending_task is not None and not pending_task.done():
            pending_task.cancel()
            try:
                await pending_task
            except Exception:
                pass
        if ws is not None and not self._ws_is_closed(ws):
            try:
                await ws.close()
            except Exception:
                pass
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except Exception:
                pass
        await self._debug("session closed")

    async def _handle_messages(self):
        if self._ws is None:
            return
        try:
            async for raw in self._ws:
                event = json.loads(raw)
                event_type = event.get("type", "")
                if event_type == "error":
                    err_payload = event.get("error", event)
                    err_msg = json.dumps(err_payload, ensure_ascii=False)
                    self._last_error_message = err_msg
                    err_type = ""
                    err_code = ""
                    if isinstance(err_payload, dict):
                        err_type = str(err_payload.get("type", "")).lower()
                        err_code = str(err_payload.get("code", "")).lower()
                    lowered = err_msg.lower()
                    # session 还没 updated 的错，或明确的致命错误，判定为 fatal
                    fatal_signatures = (
                        "model not found",
                        "unauthorized",
                        "invalid api key",
                        "forbidden",
                        "quota",
                        "insufficient",
                    )
                    is_fatal = (not self._session_updated) or any(
                        sig in lowered for sig in fatal_signatures
                    )
                    if is_fatal:
                        self._connected = False
                        self._fatal_error = True
                        self._ready_event.set()
                        await self._debug(f"server fatal error: {err_msg}")
                    else:
                        self._responding = False
                        self._image_sent_for_turn = False
                        self._audio_appended_for_turn = False
                        self._audio_append_count_for_turn = 0
                        self._audio_append_count_after_speech_started = 0
                        self._speech_started_for_turn = False
                        self._cancel_pending_image_task()
                        await self._debug(
                            f"server recoverable error (type={err_type} code={err_code}): {err_msg}"
                        )
                    await self.on_error(err_msg)
                    continue
                if event_type == "session.created":
                    self._connected = True
                    await self._debug("event session.created")
                    continue
                if event_type == "session.updated":
                    self._connected = True
                    self._session_updated = True
                    self._ready_event.set()
                    await self._debug("event session.updated")
                    continue
                if event_type == "response.created":
                    self._responding = True
                    await self._debug("event response.created")
                    await self.on_response_started()
                    continue
                if event_type == "response.done":
                    self._responding = False
                    self._image_sent_for_turn = False
                    self._audio_appended_for_turn = False
                    self._audio_append_count_for_turn = 0
                    self._audio_append_count_after_speech_started = 0
                    self._speech_started_for_turn = False
                    self._cancel_pending_image_task()
                    await self._debug("event response.done")
                    await self.on_response_done()
                    continue
                if event_type == "input_audio_buffer.speech_started":
                    self._image_sent_for_turn = False
                    self._speech_started_for_turn = True
                    self._audio_append_count_after_speech_started = 0
                    await self._debug("event speech_started")
                    await self.on_speech_started()
                    self._schedule_image_append()
                    continue
                if event_type == "input_audio_buffer.speech_stopped":
                    await self._debug("event speech_stopped")
                    continue
                if event_type == "conversation.item.input_audio_transcription.completed":
                    transcript = (event.get("transcript") or "").strip()
                    if transcript:
                        await self._debug(f"input transcript: {transcript}")
                        await self.on_input_transcript(transcript)
                    continue
                if event_type in ("response.audio_transcript.delta", "response.text.delta"):
                    delta = event.get("delta") or ""
                    if delta:
                        await self.on_output_text_delta(delta)
                    continue
                if event_type == "response.audio.delta":
                    delta = event.get("delta") or ""
                    if delta:
                        try:
                            audio_bytes = base64.b64decode(delta)
                        except Exception:
                            continue
                        if audio_bytes:
                            await self._debug(f"audio delta bytes={len(audio_bytes)}")
                            await self.on_output_audio(audio_bytes)
        except ConnectionClosed as exc:
            self._connected = False
            self._responding = False
            self._last_close_message = f"code={exc.code}, reason={exc.reason}"
            if "Model not found" in (exc.reason or ""):
                self._fatal_error = True
            self._ready_event.set()
            await self._debug(f"connection closed code={exc.code} reason={exc.reason}")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._connected = False
            self._last_error_message = str(exc)
            self._ready_event.set()
            await self._debug(f"message loop exception: {exc}")
            await self.on_error(str(exc))

    def _cancel_pending_image_task(self):
        task = self._pending_image_task
        self._pending_image_task = None
        if task is not None and not task.done():
            task.cancel()

    def _schedule_image_append(self):
        task = self._pending_image_task
        if task is not None and not task.done():
            return
        self._pending_image_task = asyncio.create_task(self._append_latest_image_when_ready())

    async def _append_latest_image_when_ready(self):
        try:
            # 视觉模式改成按需抓单帧后，需要给 ESP32 留出一点额外时间上传这帧。
            for _ in range(50):
                if self._image_sent_for_turn:
                    return
                if not self._speech_started_for_turn:
                    return
                # 关键点：必须等到 speech_started 之后又真正 append 了音频，
                # 不能拿 speech_started 之前累计的音频数来判断，否则服务端仍可能认定
                # 这张图“早于本轮音频”。
                if self._audio_append_count_after_speech_started >= 1:
                    await asyncio.sleep(0.08)
                    await self._append_latest_image()
                    if self._image_sent_for_turn:
                        return
                await asyncio.sleep(0.03)
        except asyncio.CancelledError:
            raise
        finally:
            if self._pending_image_task is asyncio.current_task():
                self._pending_image_task = None

    async def _append_latest_image(self):
        if self._ws is None or self.get_latest_image_bytes is None or self._image_sent_for_turn:
            return
        # 服务端要求本轮必须先有 audio 才能 append image，否则会回
        # invalid_request_error "Error append image before append audio"
        if not self._audio_appended_for_turn:
            await self._debug("skip image append: no audio in current turn yet")
            return
        jpeg_bytes = self.get_latest_image_bytes() or b""
        if not jpeg_bytes:
            return
        payload = base64.b64encode(jpeg_bytes).decode("ascii")
        try:
            await self._send_event({"type": "input_image_buffer.append", "image": payload})
            self._image_sent_for_turn = True
            await self._debug(
                "image append sent after "
                f"turn_audio_count={self._audio_append_count_for_turn} "
                f"post_speech_audio_count={self._audio_append_count_after_speech_started}"
            )
        except Exception as exc:
            await self._debug(f"image append failed, will retry next turn: {exc}")

    def _build_closed_error(self, default_message: str) -> str:
        detail = self._last_error_message or self._last_close_message or default_message
        return f"Realtime 连接不可用：{detail}"

    async def _debug(self, message: str):
        if self.on_debug is not None:
            await self.on_debug(message)
