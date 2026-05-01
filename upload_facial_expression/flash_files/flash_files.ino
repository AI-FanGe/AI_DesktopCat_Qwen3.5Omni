/*
 * 文件写入工具 v4.0 (分批上传版)
 * 
 * ============================================
 * 使用步骤：
 * ============================================
 * 
 * 步骤0：生成所有头文件（只需运行一次）
 *   cd flash_files目录
 *   python generate_all_headers.py
 * 
 * 步骤1：上传批次1（格式化+写入）
 *   - 确保下面 BATCH_NUMBER 设为 1
 *   - 上传程序
 *   - 串口输入 F 格式化
 *   - 串口输入 W 写入
 * 
 * 步骤2-5：上传剩余批次（只写入）
 *   - 把 BATCH_NUMBER 改成 2、3、4、5...
 *   - 上传程序
 *   - 串口只输入 W（不要格式化！）
 * 
 * 完成后：烧录 test_all_modules.ino
 */

#include <LittleFS.h>

// ============================================
// 修改这个数字切换批次 (1, 2, 3, 4, 5...)
// ============================================
#define BATCH_NUMBER 13
// ============================================

// 根据批次号包含对应的头文件
#if BATCH_NUMBER == 1
  #include "batch_1.h"
#elif BATCH_NUMBER == 2
  #include "batch_2.h"
#elif BATCH_NUMBER == 3
  #include "batch_3.h"
#elif BATCH_NUMBER == 4
  #include "batch_4.h"
#elif BATCH_NUMBER == 5
  #include "batch_5.h"
#elif BATCH_NUMBER == 6
  #include "batch_6.h"
#elif BATCH_NUMBER == 7
  #include "batch_7.h"
#elif BATCH_NUMBER == 8
  #include "batch_8.h"
#elif BATCH_NUMBER == 9
  #include "batch_9.h"
#elif BATCH_NUMBER == 10
  #include "batch_10.h"
#elif BATCH_NUMBER == 11
  #include "batch_11.h"
#elif BATCH_NUMBER == 12
  #include "batch_12.h"
#elif BATCH_NUMBER == 13
  #include "batch_13.h"

  
#else
  #error "BATCH_NUMBER 必须是 1-8"
#endif

bool fsReady = false;

void setup() {
  Serial.begin(115200);
  delay(2000);
  
  Serial.println("\n╔═══════════════════════════════════════════╗");
  Serial.printf("║     文件写入工具 - 批次 %d                  ║\n", BATCH_NUMBER);
  Serial.println("╚═══════════════════════════════════════════╝\n");
  
  Serial.println("初始化LittleFS...");
  if (!LittleFS.begin(true)) {
    Serial.println("错误: LittleFS初始化失败!");
    while(1) delay(100);
  }
  fsReady = true;
  
  showStatus();
  showMenu();
}

void showStatus() {
  Serial.println("\n-------- 存储状态 --------");
  Serial.printf("总空间: %d KB (%.1f MB)\n", 
                LittleFS.totalBytes() / 1024,
                LittleFS.totalBytes() / 1024.0 / 1024.0);
  Serial.printf("已用:   %d KB\n", LittleFS.usedBytes() / 1024);
  Serial.printf("可用:   %d KB\n", (LittleFS.totalBytes() - LittleFS.usedBytes()) / 1024);
  Serial.printf("当前批次: %d (%d 个文件)\n", BATCH_NUMBER, EMBEDDED_FILE_COUNT);
  Serial.println("---------------------------\n");
}

void showMenu() {
  Serial.println("╔═══════════════════════════════════════════╗");
  Serial.println("║  命令:                                    ║");
  Serial.println("║    F - 格式化（清空所有数据）             ║");
  Serial.println("║    W - 写入文件                           ║");
  Serial.println("║    L - 列出已有文件                       ║");
  Serial.println("║    S - 显示存储状态                       ║");
  Serial.println("╚═══════════════════════════════════════════╝");
  
  if (BATCH_NUMBER == 1) {
    Serial.println("\n★ 第1批: 先输入F格式化，再输入W写入");
  } else {
    Serial.println("\n★ 后续批次: 只输入W写入（不要格式化！）");
  }
}

void formatFS() {
  Serial.println("\n[格式化] 正在清空所有数据...");
  unsigned long start = millis();
  
  LittleFS.format();
  LittleFS.begin();
  
  Serial.printf("[格式化] 完成 (用时 %.1f 秒)\n", (millis() - start) / 1000.0);
  showStatus();
}

void writeFiles() {
  Serial.printf("\n[写入] 批次%d: 开始写入 %d 个文件...\n", BATCH_NUMBER, EMBEDDED_FILE_COUNT);
  Serial.println("========================================");
  
  int success = 0;
  int failed = 0;
  uint32_t totalBytes = 0;
  unsigned long start = millis();
  
  for (int i = 0; i < EMBEDDED_FILE_COUNT; i++) {
    const EmbeddedFile& ef = embeddedFiles[i];
    
    int progress = ((i + 1) * 100) / EMBEDDED_FILE_COUNT;
    Serial.printf("[%3d%%] %s (%d bytes)... ", progress, ef.path, ef.size);
    
    // 创建目录
    String path = ef.path;
    int lastSlash = path.lastIndexOf('/');
    if (lastSlash > 0) {
      String dir = path.substring(0, lastSlash);
      createDirs(dir.c_str());
    }
    
    // 写入文件
    File file = LittleFS.open(ef.path, "w");
    if (!file) {
      Serial.println("✗ 打开失败");
      failed++;
      continue;
    }
    
    // 从PROGMEM读取并写入
    uint8_t buffer[256];
    uint32_t written = 0;
    
    while (written < ef.size) {
      int toWrite = min((uint32_t)256, ef.size - written);
      for (int j = 0; j < toWrite; j++) {
        buffer[j] = pgm_read_byte(ef.data + written + j);
      }
      file.write(buffer, toWrite);
      written += toWrite;
    }
    
    file.close();
    
    if (written == ef.size) {
      Serial.println("✓");
      success++;
      totalBytes += ef.size;
    } else {
      Serial.printf("✗ 只写入 %d/%d\n", written, ef.size);
      failed++;
    }
    
    yield();
  }
  
  float elapsed = (millis() - start) / 1000.0;
  
  Serial.println("\n========================================");
  Serial.printf("批次%d写入完成: %d 成功, %d 失败\n", BATCH_NUMBER, success, failed);
  Serial.printf("用时: %.1f 秒\n", elapsed);
  
  showStatus();
  
  if (failed == 0) {
    Serial.println("\n✓ 批次写入成功！");
    Serial.printf("  下一步: 修改 BATCH_NUMBER 为 %d，重新上传\n", BATCH_NUMBER + 1);
  }
}

void listFiles() {
  Serial.println("\n-------- 文件列表 --------");
  int count = 0;
  listDir("/", 0, count);
  Serial.printf("共 %d 个文件\n", count);
  Serial.println("---------------------------");
}

void listDir(const char* dirname, int level, int& count) {
  File root = LittleFS.open(dirname);
  if (!root || !root.isDirectory()) return;
  
  File file = root.openNextFile();
  while (file) {
    for (int i = 0; i < level; i++) Serial.print("  ");
    
    if (file.isDirectory()) {
      Serial.printf("[%s]\n", file.name());
      String subPath = String(dirname);
      if (!subPath.endsWith("/")) subPath += "/";
      subPath += file.name();
      listDir(subPath.c_str(), level + 1, count);
    } else {
      Serial.printf("%s (%d bytes)\n", file.name(), file.size());
      count++;
    }
    file = root.openNextFile();
  }
}

void createDirs(const char* path) {
  String pathStr = path;
  String current = "";
  
  for (int i = 0; i < pathStr.length(); i++) {
    current += pathStr[i];
    if (pathStr[i] == '/' && current.length() > 1) {
      if (!LittleFS.exists(current)) {
        LittleFS.mkdir(current);
      }
    }
  }
  if (!LittleFS.exists(current) && current.length() > 0) {
    LittleFS.mkdir(current);
  }
}

void loop() {
  if (Serial.available()) {
    char cmd = Serial.read();
    while (Serial.available()) Serial.read();
    
    switch (cmd) {
      case 'F': case 'f': formatFS(); break;
      case 'W': case 'w': writeFiles(); break;
      case 'L': case 'l': listFiles(); break;
      case 'S': case 's': showStatus(); break;
      default:
        if (cmd >= 32) {
          Serial.printf("未知命令: %c\n", cmd);
          showMenu();
        }
        break;
    }
  }
  delay(10);
}
