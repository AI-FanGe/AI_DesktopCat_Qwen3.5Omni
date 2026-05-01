# -*- coding: utf-8 -*-
import asyncio
from collections import deque
from dataclasses import dataclass
from typing import Deque, Optional, Set, List

from fastapi import Request
from fastapi.responses import StreamingResponse

STREAM_SR = 24000
STREAM_CH = 1
STREAM_SW = 2
BYTES_PER_20MS_16K = STREAM_SR * STREAM_SW * 20 // 1000
SILENCE_20MS = bytes(BYTES_PER_20MS_16K)

current_ai_task: Optional[asyncio.Task] = None


async def cancel_current_ai():
    global current_ai_task
    task = current_ai_task
    current_ai_task = None
    if task and not task.done():
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception:
            pass


def is_playing_now() -> bool:
    task = current_ai_task
    return task is not None and not task.done()


@dataclass(frozen=True)
class StreamClient:
    q: asyncio.Queue
    abort_event: asyncio.Event


stream_clients: "Set[StreamClient]" = set()
# 原来是 240 = 约 4.8 秒，容易在长回复/突发音频包时被挤爆。
# 放大到约 60 秒缓冲，避免上游 realtime 丢 chunk 时整段音频断掉。
STREAM_QUEUE_MAX = 3000
# ESP32 在收到 TTS_START 后需要一点时间重新连上 /stream.wav。
# 保留一小段最近音频，避免句首在建连窗口里直接丢掉。
STREAM_PREBUFFER_CHUNKS = 80
# 浏览器 Audio 打开 /stream.wav 后，Realtime 建连/首包可能需要 1-2 秒。
# 空闲宽限太短会让本地播放看起来像“自己断开”。
STREAM_IDLE_GRACE_SEC = 3.0
recent_pcm_chunks: Deque[bytes] = deque(maxlen=STREAM_PREBUFFER_CHUNKS)


def _wav_header_unknown_size(sr=16000, ch=1, sw=2) -> bytes:
    import struct

    byte_rate = sr * ch * sw
    block_align = ch * sw
    data_size = 0x7FFFFFF0
    riff_size = 36 + data_size
    return struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF",
        riff_size,
        b"WAVE",
        b"fmt ",
        16,
        1,
        ch,
        sr,
        byte_rate,
        block_align,
        sw * 8,
        b"data",
        data_size,
    )


async def hard_reset_audio(reason: str = ""):
    for client in list(stream_clients):
        try:
            client.abort_event.set()
        except Exception:
            pass
    stream_clients.clear()
    recent_pcm_chunks.clear()
    await cancel_current_ai()
    if reason:
        print(f"[HARD-RESET] {reason}", flush=True)


def clear_stream_prebuffer():
    recent_pcm_chunks.clear()


async def broadcast_pcm16_realtime(pcm16: bytes):
    # 这里只做“立刻塞进每个客户端队列”的工作，不再做 real-time 节拍。
    # 真正的节拍由 ESP32 端 I2S 播放 + HTTP 背压天然限速；服务端再 sleep 只会把
    # upstream Realtime WebSocket 的接收任务卡死，导致长对话后上游堆积、断流。
    offset = 0
    dead: List[StreamClient] = []
    while offset < len(pcm16):
        take = min(BYTES_PER_20MS_16K, len(pcm16) - offset)
        piece = pcm16[offset : offset + take]
        recent_pcm_chunks.append(piece)
        for client in list(stream_clients):
            if client.abort_event.is_set():
                dead.append(client)
                continue
            try:
                if client.q.full():
                    try:
                        client.q.get_nowait()
                    except Exception:
                        pass
                client.q.put_nowait(piece)
            except Exception:
                dead.append(client)
        offset += take
    if dead:
        for client in dead:
            stream_clients.discard(client)
    # 给事件循环一次机会调度其他 coroutine（例如 HTTP 消费 / 上游接收）
    await asyncio.sleep(0)


def register_stream_route(app):
    @app.get("/stream.wav")
    async def stream_wav(_: Request):
        for client in list(stream_clients):
            try:
                client.abort_event.set()
            except Exception:
                pass
        stream_clients.clear()

        queue: asyncio.Queue[bytes | None] = asyncio.Queue(maxsize=STREAM_QUEUE_MAX)
        abort_event = asyncio.Event()
        client = StreamClient(q=queue, abort_event=abort_event)
        stream_clients.add(client)
        for chunk in recent_pcm_chunks:
            try:
                queue.put_nowait(chunk)
            except Exception:
                break

        async def gen():
            loop = asyncio.get_running_loop()
            last_emit_at = loop.time()
            yield _wav_header_unknown_size(STREAM_SR, STREAM_CH, STREAM_SW)
            try:
                while True:
                    if abort_event.is_set():
                        break
                    try:
                        chunk = await asyncio.wait_for(queue.get(), timeout=0.12)
                    except asyncio.TimeoutError:
                        if is_playing_now():
                            yield SILENCE_20MS
                            last_emit_at = loop.time()
                            continue
                        if loop.time() - last_emit_at > STREAM_IDLE_GRACE_SEC:
                            break
                        continue
                    if abort_event.is_set() or chunk is None:
                        break
                    if chunk:
                        if len(chunk) < BYTES_PER_20MS_16K:
                            chunk = chunk + (b"\x00" * (BYTES_PER_20MS_16K - len(chunk)))
                        yield chunk
                        last_emit_at = loop.time()
            finally:
                stream_clients.discard(client)

        return StreamingResponse(gen(), media_type="audio/wav")
