import cv2
import os
from pathlib import Path

# ==================== 配置区域 ====================
VIDEO_PATH = r"E:\沙粒云\自媒体\2026视频制作\20260422googlecloudnest\servo_control\reference\表情\8.mp4"              # 输入视频路径
OUTPUT_DIR = r"E:\沙粒云\自媒体\2026视频制作\20260422googlecloudnest\servo_control\reference\data\anim8"                  # 输出文件夹名称
TARGET_SIZE = (284, 240)              # 目标尺寸 (宽, 高)
TARGET_FRAME_COUNT = 80               # 目标输出帧数（无论视频长短，均匀截取这么多帧）
JPEG_QUALITY = 60                     # JPEG质量 (0-100)
CROP_OFFSET = 25                      # 四周裁剪像素数（去除边缘黑边/噪点）
# =================================================


def video_to_jpeg_frames(video_path, output_dir, target_size=(240, 240), target_frame_count=80, quality=85):
    """
    将视频均匀截取为指定数量的JPEG帧序列

    参数:
        video_path: 输入视频文件路径
        output_dir: 输出目录路径
        target_size: 目标尺寸 (width, height)，默认240x240匹配TFT屏幕
        target_frame_count: 目标输出帧数，无论视频长短均匀采样
        quality: JPEG质量 (0-100)，默认85
    """

    # 检查视频文件是否存在
    if not os.path.exists(video_path):
        print(f"错误：视频文件不存在: {video_path}")
        return

    # 创建输出目录
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    # 打开视频
    cap = cv2.VideoCapture(video_path)

    if not cap.isOpened():
        print(f"错误：无法打开视频文件: {video_path}")
        return

    # 获取视频信息
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    original_fps = cap.get(cv2.CAP_PROP_FPS)
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

    print(f"视频信息:")
    print(f"  - 分辨率: {width}x{height}")
    print(f"  - 原始帧率: {original_fps:.2f} FPS")
    print(f"  - 总帧数: {total_frames}")
    print(f"  - 目标尺寸: {target_size[0]}x{target_size[1]}")
    print(f"  - 目标输出帧数: {target_frame_count}")
    print(f"  - JPEG质量: {quality}")

    if total_frames <= 0:
        print("错误：无法读取视频帧数")
        cap.release()
        return

    if target_frame_count <= 0:
        print("错误：目标帧数必须大于0")
        cap.release()
        return

    # 计算要采样的帧索引（在 [0, total_frames-1] 范围内均匀取 target_frame_count 个点）
    if target_frame_count == 1:
        sample_indices = [total_frames // 2]
    else:
        step = (total_frames - 1) / (target_frame_count - 1)
        sample_indices = [int(round(i * step)) for i in range(target_frame_count)]

    # 去重并确保在合法范围内（极短视频可能出现重复索引）
    seen = set()
    unique_indices = []
    for idx in sample_indices:
        idx = max(0, min(total_frames - 1, idx))
        if idx not in seen:
            seen.add(idx)
            unique_indices.append(idx)

    # 若去重后数量不足 target_frame_count（视频总帧数 < target_frame_count），
    # 通过循环重复最后的索引补齐，保证输出固定数量
    while len(unique_indices) < target_frame_count:
        unique_indices.append(unique_indices[-1] if unique_indices else 0)

    # 保持原始采样顺序（可能含重复），用于实际保存
    final_indices = []
    for idx in sample_indices:
        final_indices.append(max(0, min(total_frames - 1, idx)))

    print(f"  - 采样步长: {(total_frames - 1) / max(1, target_frame_count - 1):.3f} 帧")
    print(f"\n开始转换...")

    saved_count = 0

    try:
        for i, target_idx in enumerate(final_indices):
            cap.set(cv2.CAP_PROP_POS_FRAMES, target_idx)
            ret, frame = cap.read()

            if not ret or frame is None:
                print(f"\n警告：无法读取第 {target_idx} 帧，跳过")
                continue

            # 四周裁剪
            h, w = frame.shape[:2]
            if CROP_OFFSET > 0 and h > CROP_OFFSET * 2 and w > CROP_OFFSET * 2:
                frame = frame[CROP_OFFSET:h - CROP_OFFSET, CROP_OFFSET:w - CROP_OFFSET]

            # 调整大小
            resized_frame = cv2.resize(frame, target_size, interpolation=cv2.INTER_AREA)

            saved_count += 1
            output_filename = output_path / f"{saved_count:04d}.jpg"

            encode_param = [int(cv2.IMWRITE_JPEG_QUALITY), quality]
            cv2.imwrite(str(output_filename), resized_frame, encode_param)

            if saved_count % 10 == 0 or saved_count == target_frame_count:
                progress = (saved_count / target_frame_count) * 100
                print(f"  进度: {progress:.1f}% - 已保存 {saved_count}/{target_frame_count} 帧", end='\r')

        print(f"\n\n转换完成!")
        print(f"  - 保存帧数: {saved_count}")
        print(f"  - 输出目录: {output_path.absolute()}")

    except Exception as e:
        print(f"\n错误：转换过程中出现异常: {e}")

    finally:
        cap.release()


def main():
    print("=" * 60)
    print("视频转JPEG帧 - 均匀采样版 (固定帧数)")
    print("=" * 60)
    print(f"\n当前配置:")
    print(f"  输入视频: {VIDEO_PATH}")
    print(f"  输出目录: {OUTPUT_DIR}")
    print(f"  目标尺寸: {TARGET_SIZE[0]}x{TARGET_SIZE[1]}")
    print(f"  目标帧数: {TARGET_FRAME_COUNT} 帧 (均匀采样)")
    print(f"  JPEG质量: {JPEG_QUALITY}")
    print(f"\n如需修改，请编辑代码顶部的配置区域\n")
    print("=" * 60)
    print()

    video_to_jpeg_frames(
        video_path=VIDEO_PATH,
        output_dir=OUTPUT_DIR,
        target_size=TARGET_SIZE,
        target_frame_count=TARGET_FRAME_COUNT,
        quality=JPEG_QUALITY
    )


if __name__ == '__main__':
    main()
