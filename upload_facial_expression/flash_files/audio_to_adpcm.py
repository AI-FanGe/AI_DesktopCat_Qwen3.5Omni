#!/usr/bin/env python3
"""
音频转 IMA ADPCM 工具

将 WAV/MP3 等音频文件转换为 .adp 格式（IMA ADPCM 编码）
压缩比 4:1，音质接近原始 PCM，适合 ESP32 小喇叭播放

文件格式 (.adp):
  Header (16 bytes):
    magic[4]     = "ADPM"
    sampleRate   = uint32 (默认 16000)
    numSamples   = uint32 (解码后的总采样数)
    dataSize     = uint32 (ADPCM 数据字节数)
  Data:
    IMA ADPCM 编码数据 (每字节2个采样，低nibble在前)

用法:
  python audio_to_adpcm.py input.wav              # 转为 s1.adp
  python audio_to_adpcm.py input.mp3 -n 3         # 转为 s3.adp
  python audio_to_adpcm.py input.wav -r 22050      # 指定采样率
  python audio_to_adpcm.py input.wav -o custom.adp # 指定输出名

依赖: pip install pydub (需要 ffmpeg)
"""

import os
import sys
import struct
import argparse
import array
import math

# ====================================================================
# IMA ADPCM 编码表
# ====================================================================
IMA_STEP_TABLE = [
    7, 8, 9, 10, 11, 12, 13, 14, 16, 17,
    19, 21, 23, 25, 28, 31, 34, 37, 41, 45,
    50, 55, 60, 66, 73, 80, 88, 97, 107, 118,
    130, 143, 157, 173, 190, 209, 230, 253, 279, 307,
    337, 371, 408, 449, 494, 544, 598, 658, 724, 796,
    876, 963, 1060, 1166, 1282, 1411, 1552, 1707, 1878, 2066,
    2272, 2499, 2749, 3024, 3327, 3660, 4026, 4428, 4871, 5358,
    5894, 6484, 7132, 7845, 8630, 9493, 10442, 11487, 12635, 13899,
    15289, 16818, 18500, 20350, 22385, 24623, 27086, 29794, 32767
]

IMA_INDEX_TABLE = [-1, -1, -1, -1, 2, 4, 6, 8, -1, -1, -1, -1, 2, 4, 6, 8]


def encode_ima_adpcm(samples):
    """
    将 16-bit PCM 采样编码为 IMA ADPCM

    参数:
        samples: list/array of int16 采样值

    返回:
        bytes: ADPCM 编码数据（每字节2个采样，低nibble在前）
    """
    predictor = 0
    step_index = 0
    output = bytearray()
    nibble_buf = None

    for sample in samples:
        sample = max(-32768, min(32767, int(sample)))
        diff = sample - predictor
        step = IMA_STEP_TABLE[step_index]

        nibble = 0
        if diff < 0:
            nibble = 8
            diff = -diff

        if diff >= step:
            nibble |= 4
            diff -= step
        if diff >= (step >> 1):
            nibble |= 2
            diff -= (step >> 1)
        if diff >= (step >> 2):
            nibble |= 1

        # 用解码逻辑更新 predictor（保持编解码同步）
        step = IMA_STEP_TABLE[step_index]
        decoded_diff = step >> 3
        if nibble & 1:
            decoded_diff += step >> 2
        if nibble & 2:
            decoded_diff += step >> 1
        if nibble & 4:
            decoded_diff += step
        if nibble & 8:
            decoded_diff = -decoded_diff

        predictor = max(-32768, min(32767, predictor + decoded_diff))
        step_index = max(0, min(88, step_index + IMA_INDEX_TABLE[nibble & 0x0F]))

        if nibble_buf is None:
            nibble_buf = nibble & 0x0F
        else:
            output.append(nibble_buf | ((nibble & 0x0F) << 4))
            nibble_buf = None

    if nibble_buf is not None:
        output.append(nibble_buf)

    return bytes(output)


def load_audio(filepath, target_rate=16000):
    """
    加载音频文件并转为目标采样率的单声道 16-bit PCM

    返回:
        (samples: list[int16], sample_rate: int, duration_sec: float)
    """
    try:
        from pydub import AudioSegment
    except ImportError:
        print("错误: 需要安装 pydub")
        print("  pip install pydub")
        print("  还需要安装 ffmpeg: https://ffmpeg.org/download.html")
        sys.exit(1)

    ext = os.path.splitext(filepath)[1].lower()
    if ext in ('.wav', '.mp3', '.ogg', '.flac', '.m4a', '.aac', '.wma'):
        audio = AudioSegment.from_file(filepath)
    else:
        print(f"尝试加载未知格式: {ext}")
        audio = AudioSegment.from_file(filepath)

    audio = audio.set_channels(1).set_frame_rate(target_rate).set_sample_width(2)

    raw = audio.raw_data
    samples = array.array('h', raw)

    duration = len(samples) / target_rate
    return list(samples), target_rate, duration


def save_adp(filepath, sample_rate, samples, adpcm_data):
    """保存为 .adp 文件"""
    header = struct.pack('<4sIII',
                         b'ADPM',
                         sample_rate,
                         len(samples),
                         len(adpcm_data))

    with open(filepath, 'wb') as f:
        f.write(header)
        f.write(adpcm_data)


def format_size(size_bytes):
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    else:
        return f"{size_bytes / 1024 / 1024:.1f} MB"


def main():
    parser = argparse.ArgumentParser(description='音频转 IMA ADPCM (.adp) 工具')
    parser.add_argument('input', help='输入音频文件 (WAV/MP3/OGG/FLAC 等)')
    parser.add_argument('-n', '--number', type=int, default=None,
                        help='音效编号，输出为 s{N}.adp（默认自动递增）')
    parser.add_argument('-o', '--output', default=None,
                        help='指定输出文件路径（优先于 -n）')
    parser.add_argument('-r', '--rate', type=int, default=16000,
                        help='目标采样率（默认 16000，可选 22050/11025/8000）')
    parser.add_argument('-d', '--dest', default=None,
                        help='输出目录（默认 ../data/sounds/）')
    args = parser.parse_args()

    if not os.path.exists(args.input):
        print(f"错误: 文件不存在: {args.input}")
        sys.exit(1)

    output_dir = args.dest or os.path.join(os.path.dirname(__file__), '..', 'data', 'sounds')
    os.makedirs(output_dir, exist_ok=True)

    if args.output:
        output_path = args.output
    elif args.number is not None:
        output_path = os.path.join(output_dir, f's{args.number}.adp')
    else:
        n = 1
        while os.path.exists(os.path.join(output_dir, f's{n}.adp')):
            n += 1
        output_path = os.path.join(output_dir, f's{n}.adp')

    print("=" * 50)
    print("  音频转 IMA ADPCM")
    print("=" * 50)
    print(f"\n输入文件: {args.input}")
    print(f"目标采样率: {args.rate} Hz")

    print("\n加载音频...", end='', flush=True)
    samples, rate, duration = load_audio(args.input, args.rate)
    print(f" ✓")

    pcm_size = len(samples) * 2
    print(f"  时长: {duration:.2f} 秒")
    print(f"  采样数: {len(samples)}")
    print(f"  PCM 大小: {format_size(pcm_size)}")

    print("编码 ADPCM...", end='', flush=True)
    adpcm_data = encode_ima_adpcm(samples)
    print(f" ✓")

    adpcm_size = len(adpcm_data)
    ratio = pcm_size / adpcm_size if adpcm_size > 0 else 0
    print(f"  ADPCM 大小: {format_size(adpcm_size)}")
    print(f"  压缩比: {ratio:.1f}:1")
    print(f"  码率: {adpcm_size / duration / 1024:.1f} KB/s")

    save_adp(output_path, rate, samples, adpcm_data)
    print(f"\n✓ 已保存: {output_path}")
    print(f"  文件大小: {format_size(os.path.getsize(output_path))}")
    print(f"  时长: {duration:.2f}s")

    print("\n" + "=" * 50)
    print("提示: 将 sounds 目录放入 data/ 后运行 generate_all_headers.py")
    print("  音效会自动被包含在批次头文件中")
    print("=" * 50)


if __name__ == '__main__':
    main()
