#!/usr/bin/env python3
"""
一次性生成所有批次的头文件

运行一次，生成 batch_1.h 到 batch_5.h
然后在Arduino代码中改 BATCH_NUMBER 即可切换
"""

import os
import sys

DATA_DIR = os.path.join(os.path.dirname(__file__), '..', 'data')
FILES_PER_BATCH = 50  # 每批50个文件，约750KB，确保编译不超限

def convert_file_to_hex(filepath):
    with open(filepath, 'rb') as f:
        data = f.read()
    
    hex_values = [f'0x{byte:02X}' for byte in data]
    lines = [','.join(hex_values[i:i+16]) for i in range(0, len(hex_values), 16)]
    return ',\n  '.join(lines), len(data)

def collect_all_files():
    """收集所有文件 - 自动扫描data目录下的所有子文件夹"""
    files = []
    
    # 自动扫描data目录下的所有子文件夹
    if not os.path.isdir(DATA_DIR):
        print(f"错误：数据目录不存在: {DATA_DIR}")
        return files
    
    # 获取所有子文件夹
    subdirs = [d for d in os.listdir(DATA_DIR) 
               if os.path.isdir(os.path.join(DATA_DIR, d))]
    subdirs.sort()  # 按名称排序
    
    print(f"扫描到的文件夹: {', '.join(subdirs)}")
    
    for anim_dir in subdirs:
        anim_path = os.path.join(DATA_DIR, anim_dir)
        file_count = 0
        for filename in sorted(os.listdir(anim_path)):
            filepath = os.path.join(anim_path, filename)
            if os.path.isfile(filepath):
                remote_path = f"/{anim_dir}/{filename}"
                files.append((remote_path, filepath))
                file_count += 1
        if file_count > 0:
            print(f"  {anim_dir}: {file_count} 个文件")
    
    return files

def generate_batch_header(batch_num, files, output_dir):
    """生成一个批次的头文件"""
    output_file = os.path.join(output_dir, f'batch_{batch_num}.h')
    
    with open(output_file, 'w', encoding='utf-8') as f:
        f.write(f'// 批次 {batch_num} - 共 {len(files)} 个文件\n')
        f.write('// 自动生成，请勿手动编辑\n\n')
        f.write('#ifndef EMBEDDED_FILES_H\n')
        f.write('#define EMBEDDED_FILES_H\n\n')
        f.write('#include <Arduino.h>\n\n')
        
        total_size = 0
        
        for idx, (rel_path, filepath) in enumerate(files):
            hex_data, size = convert_file_to_hex(filepath)
            total_size += size
            
            f.write(f'// {rel_path} ({size} bytes)\n')
            f.write(f'const uint8_t file_{idx}_data[] PROGMEM = {{\n  ')
            f.write(hex_data)
            f.write('\n};\n\n')
        
        f.write('struct EmbeddedFile {\n')
        f.write('  const char* path;\n')
        f.write('  const uint8_t* data;\n')
        f.write('  uint32_t size;\n')
        f.write('};\n\n')
        
        f.write(f'const int EMBEDDED_FILE_COUNT = {len(files)};\n\n')
        
        f.write('const EmbeddedFile embeddedFiles[] = {\n')
        for idx, (rel_path, filepath) in enumerate(files):
            size = os.path.getsize(filepath)
            comma = ',' if idx < len(files) - 1 else ''
            f.write(f'  {{"{rel_path}", file_{idx}_data, {size}}}{comma}\n')
        f.write('};\n\n')
        
        f.write('#endif // EMBEDDED_FILES_H\n')
    
    return total_size

def main():
    print("\n" + "=" * 50)
    print("  批量生成头文件")
    print("=" * 50 + "\n")
    print(f"数据目录: {DATA_DIR}\n")
    
    # 收集所有文件
    all_files = collect_all_files()
    print(f"\n总计找到 {len(all_files)} 个文件")
    
    # 分批
    batches = []
    for i in range(0, len(all_files), FILES_PER_BATCH):
        batch = all_files[i:i + FILES_PER_BATCH]
        batches.append(batch)
    
    print(f"分成 {len(batches)} 批，每批最多 {FILES_PER_BATCH} 个文件\n")
    
    # 生成每个批次的头文件
    output_dir = os.path.dirname(__file__)
    
    for batch_num, batch_files in enumerate(batches, 1):
        print(f"生成 batch_{batch_num}.h ({len(batch_files)} 个文件)... ", end='', flush=True)
        total_size = generate_batch_header(batch_num, batch_files, output_dir)
        print(f"✓ ({total_size/1024:.0f} KB)")
    
    print("\n" + "=" * 50)
    print("✓ 全部生成完成！")
    print("=" * 50)
    print(f"\n生成了 {len(batches)} 个头文件:")
    for i in range(1, len(batches) + 1):
        print(f"  batch_{i}.h")
    
    print("\n操作步骤:")
    print("  1. 打开 flash_files.ino")
    print("  2. 修改 #define BATCH_NUMBER 1")
    print("  3. 上传程序，串口输入 F 格式化，再输入 W 写入")
    print("  4. 修改为 BATCH_NUMBER 2，上传，只输入 W")
    print("  5. 重复直到所有批次上传完成")
    print(f"  6. 共需要上传 {len(batches)} 次")

if __name__ == '__main__':
    main()
















