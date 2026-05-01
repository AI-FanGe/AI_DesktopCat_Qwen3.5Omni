#!/usr/bin/env python3
"""
生成嘴部动画上传头文件

使用方法：
1. 把 5 套嘴部素材分别放到本目录下的 data 子目录，例如：
   data/mouth_closed/0001.jpg
   data/mouth_small_open/0001.jpg
   data/mouth_big_open/0001.jpg
   data/mouth_wide/0001.jpg
   data/mouth_round/0001.jpg

2. 运行：
   python generate_mouth_headers.py

3. 会生成 mouth_batch_1.h、mouth_batch_2.h ...
4. 再烧录 mouth_flash_files.ino，串口输入 W 写入

说明：
- 烧录 mouth_flash_files.ino 时，要使用本目录下同样的 custom partitions.csv
- 不要用 F 格式化，否则会清空你已经上传的 anim1~anim8
- 只要目录名和现有动画目录不同，就不会覆盖原来的动画
"""

import os
import cv2

BASE_DIR = os.path.dirname(__file__)
DATA_DIR = os.path.join(BASE_DIR, "data")
FILES_PER_BATCH = 40


def convert_bytes_to_hex(data):
    hex_values = [f"0x{byte:02X}" for byte in data]
    lines = [",".join(hex_values[i:i + 16]) for i in range(0, len(hex_values), 16)]
    return ",\n  ".join(lines), len(data)


def build_file_entry(remote_path, filepath):
    name = os.path.basename(filepath)
    lower_name = name.lower()
    if lower_name.endswith(".png") and ("遮罩" in name or "mask" in lower_name):
        image = cv2.imread(filepath, cv2.IMREAD_COLOR)
        if image is None:
            raise RuntimeError(f"无法读取遮罩图: {filepath}")
        ok, encoded = cv2.imencode(".jpg", image, [int(cv2.IMWRITE_JPEG_QUALITY), 95])
        if not ok:
            raise RuntimeError(f"遮罩图转 JPG 失败: {filepath}")
        data = encoded.tobytes()
        remote_path = "/mouth_mask.jpg"
        print(f"  遮罩图已转换: {name} -> {remote_path} ({len(data)} bytes)")
        return {"path": remote_path, "filepath": filepath, "data": data, "size": len(data)}

    with open(filepath, "rb") as f:
        data = f.read()
    return {"path": remote_path, "filepath": filepath, "data": data, "size": len(data)}


def collect_all_files():
    files = []
    if not os.path.isdir(DATA_DIR):
        print(f"错误：数据目录不存在: {DATA_DIR}")
        return files

    root_files = [f for f in os.listdir(DATA_DIR) if os.path.isfile(os.path.join(DATA_DIR, f))]
    root_files.sort()
    for filename in root_files:
        filepath = os.path.join(DATA_DIR, filename)
        files.append(build_file_entry(f"/{filename}", filepath))

    subdirs = [d for d in os.listdir(DATA_DIR) if os.path.isdir(os.path.join(DATA_DIR, d))]
    subdirs.sort()
    print(f"扫描到的嘴部目录: {', '.join(subdirs) if subdirs else '(空)'}")

    for mouth_dir in subdirs:
        mouth_path = os.path.join(DATA_DIR, mouth_dir)
        file_count = 0
        for filename in sorted(os.listdir(mouth_path)):
            filepath = os.path.join(mouth_path, filename)
            if os.path.isfile(filepath):
                files.append(build_file_entry(f"/{mouth_dir}/{filename}", filepath))
                file_count += 1
        if file_count > 0:
            print(f"  {mouth_dir}: {file_count} 个文件")

    return files


def generate_batch_header(batch_num, files):
    output_file = os.path.join(BASE_DIR, f"mouth_batch_{batch_num}.h")
    with open(output_file, "w", encoding="utf-8") as f:
        f.write(f"// 嘴部批次 {batch_num} - 共 {len(files)} 个文件\n")
        f.write("// 自动生成，请勿手动编辑\n\n")
        f.write("#ifndef EMBEDDED_MOUTH_FILES_H\n")
        f.write("#define EMBEDDED_MOUTH_FILES_H\n\n")
        f.write("#include <Arduino.h>\n\n")

        total_size = 0
        for idx, entry in enumerate(files):
            hex_data, size = convert_bytes_to_hex(entry["data"])
            total_size += size
            f.write(f"// {entry['path']} ({size} bytes)\n")
            f.write(f"const uint8_t mouth_file_{idx}_data[] PROGMEM = {{\n  ")
            f.write(hex_data)
            f.write("\n};\n\n")

        f.write("struct EmbeddedFile {\n")
        f.write("  const char* path;\n")
        f.write("  const uint8_t* data;\n")
        f.write("  uint32_t size;\n")
        f.write("};\n\n")

        f.write(f"const int EMBEDDED_FILE_COUNT = {len(files)};\n\n")
        f.write("const EmbeddedFile embeddedFiles[] = {\n")
        for idx, entry in enumerate(files):
            comma = "," if idx < len(files) - 1 else ""
            f.write(f'  {{"{entry["path"]}", mouth_file_{idx}_data, {entry["size"]}}}{comma}\n')
        f.write("};\n\n")
        f.write("#endif // EMBEDDED_MOUTH_FILES_H\n")

    return total_size


def main():
    print("\n" + "=" * 50)
    print("  生成嘴部动画上传头文件")
    print("=" * 50 + "\n")
    print(f"数据目录: {DATA_DIR}\n")

    all_files = collect_all_files()
    print(f"\n总计找到 {len(all_files)} 个文件")
    if not all_files:
        return

    batches = []
    for i in range(0, len(all_files), FILES_PER_BATCH):
        batches.append(all_files[i:i + FILES_PER_BATCH])

    print(f"分成 {len(batches)} 批，每批最多 {FILES_PER_BATCH} 个文件\n")
    for batch_num, batch_files in enumerate(batches, 1):
        print(f"生成 mouth_batch_{batch_num}.h ({len(batch_files)} 个文件)... ", end="", flush=True)
        total_size = generate_batch_header(batch_num, batch_files)
        print(f"✓ ({total_size / 1024:.0f} KB)")

    print("\n操作步骤:")
    print("  1. 打开 mouth_flash_files.ino")
    print("  2. 修改 #define BATCH_NUMBER 1")
    print("  3. 上传程序，串口只输入 W")
    print("  4. 修改为 BATCH_NUMBER 2，上传，只输入 W")
    print("  5. 重复直到所有批次上传完成")
    print("  6. 不要输入 F，否则会清空原来的动画")


if __name__ == "__main__":
    main()
