/*
 * 嘴部动画写入工具
 *
 * 作用：
 * - 把嘴部动画素材写入 LittleFS
 * - 使用新的目录名，不覆盖原来的 /anim1 ~ /anim8
 *
 * 推荐目录名：
 *   /mouth_closed
 *   /mouth_small_open
 *   /mouth_big_open
 *   /mouth_wide
 *   /mouth_round
 *
 * 使用步骤：
 * 1. 把素材放到本目录 data 子目录下
 * 2. 运行 generate_mouth_headers.py
 * 3. 修改 BATCH_NUMBER
 * 4. 烧录本程序
 * 5. 串口只输入 W
 *
 * 注意：
 * - 必须和原来的 flash_files 使用同一份 custom partitions.csv
 * - 本目录已经放了一份同样的 partitions.csv，烧录时要用它
 * - 默认保留 F 命令，但你现在不要用
 * - 用 F 会清空原有动画和嘴部素材
 */

#include <LittleFS.h>

#define BATCH_NUMBER 2

#if BATCH_NUMBER == 1
  #include "mouth_batch_1.h"
#elif BATCH_NUMBER == 2
  #include "mouth_batch_2.h"
#elif BATCH_NUMBER == 3
  #include "mouth_batch_3.h"
#elif BATCH_NUMBER == 4
  #include "mouth_batch_4.h"
#elif BATCH_NUMBER == 5
  #include "mouth_batch_5.h"
#elif BATCH_NUMBER == 6
  #include "mouth_batch_6.h"
#elif BATCH_NUMBER == 7
  #include "mouth_batch_7.h"
#elif BATCH_NUMBER == 8
  #include "mouth_batch_8.h"
#elif BATCH_NUMBER == 9
  #include "mouth_batch_9.h"
#elif BATCH_NUMBER == 10
  #include "mouth_batch_10.h"
#else
  #error "BATCH_NUMBER 超出 mouth_batch 范围，请先生成对应头文件"
#endif

void showStatus();
void showMenu();
void formatFS();
void writeFiles();
void thinAnimFolders();
void listFiles();
void listDir(const char* dirname, int level, int& count);
void createDirs(const char* path);
int countAnimFrames(const char* animPath);
bool thinSingleAnimFolder(const char* animPath);

void setup() {
  Serial.begin(115200);
  delay(2000);

  Serial.println("\n╔═══════════════════════════════════════════╗");
  Serial.printf("║   嘴部动画写入工具 - 批次 %d               ║\n", BATCH_NUMBER);
  Serial.println("╚═══════════════════════════════════════════╝\n");

  Serial.println("初始化LittleFS...");
  if (!LittleFS.begin(true)) {
    Serial.println("错误: LittleFS初始化失败!");
    while (1) delay(100);
  }

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
  Serial.println("║    H - 瘦身 anim6/7/8 (隔帧删减)          ║");
  Serial.println("║    W - 写入嘴部文件                       ║");
  Serial.println("║    L - 列出已有文件                       ║");
  Serial.println("║    S - 显示存储状态                       ║");
  Serial.println("║    F - 格式化(危险，不建议使用)           ║");
  Serial.println("╚═══════════════════════════════════════════╝");
  Serial.println("\n★ 建议顺序: 先 H 释放空间，再 W 上传嘴部；不要输入 F");
}

void formatFS() {
  Serial.println("\n[危险] 正在格式化 LittleFS...");
  unsigned long start = millis();
  LittleFS.format();
  LittleFS.begin();
  Serial.printf("[格式化] 完成 (用时 %.1f 秒)\n", (millis() - start) / 1000.0);
  showStatus();
}

void writeFiles() {
  Serial.printf("\n[写入] 嘴部批次%d: 开始写入 %d 个文件...\n", BATCH_NUMBER, EMBEDDED_FILE_COUNT);
  Serial.println("========================================");

  int success = 0;
  int failed = 0;
  uint32_t totalBytes = 0;
  unsigned long start = millis();

  for (int i = 0; i < EMBEDDED_FILE_COUNT; i++) {
    const EmbeddedFile& ef = embeddedFiles[i];
    int progress = ((i + 1) * 100) / EMBEDDED_FILE_COUNT;
    Serial.printf("[%3d%%] %s (%d bytes)... ", progress, ef.path, ef.size);

    String path = ef.path;
    int lastSlash = path.lastIndexOf('/');
    if (lastSlash > 0) {
      String dir = path.substring(0, lastSlash);
      createDirs(dir.c_str());
    }

    File file = LittleFS.open(ef.path, "w");
    if (!file) {
      Serial.println("✗ 打开失败");
      failed++;
      continue;
    }

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
  Serial.printf("嘴部批次%d写入完成: %d 成功, %d 失败\n", BATCH_NUMBER, success, failed);
  Serial.printf("用时: %.1f 秒\n", elapsed);
  showStatus();
}

int countAnimFrames(const char* animPath) {
  int count = 0;
  char filename[48];
  for (int i = 1; i <= 9999; ++i) {
    sprintf(filename, "%s/%04d.jpg", animPath, i);
    if (LittleFS.exists(filename)) {
      count = i;
    } else {
      break;
    }
  }
  return count;
}

bool thinSingleAnimFolder(const char* animPath) {
  int total = countAnimFrames(animPath);
  if (total <= 1) {
    Serial.printf("[瘦身] %s 帧数不足，跳过 (%d)\n", animPath, total);
    return true;
  }

  Serial.printf("[瘦身] %s: 原始 %d 帧 -> 保留约 %d 帧\n", animPath, total, (total + 1) / 2);

  char oldName[48];
  char tempName[48];
  char finalName[48];
  int kept = 0;

  // 先把保留帧重命名到临时文件，并删除偶数帧
  for (int i = 1; i <= total; ++i) {
    sprintf(oldName, "%s/%04d.jpg", animPath, i);
    if (!LittleFS.exists(oldName)) continue;

    if (i % 2 == 1) {
      kept++;
      sprintf(tempName, "%s/__tmp%04d.jpg", animPath, kept);
      if (!LittleFS.rename(oldName, tempName)) {
        Serial.printf("[瘦身] 重命名失败: %s -> %s\n", oldName, tempName);
        return false;
      }
    } else {
      if (!LittleFS.remove(oldName)) {
        Serial.printf("[瘦身] 删除失败: %s\n", oldName);
        return false;
      }
    }
  }

  // 再把临时文件按连续编号写回
  for (int i = 1; i <= kept; ++i) {
    sprintf(tempName, "%s/__tmp%04d.jpg", animPath, i);
    sprintf(finalName, "%s/%04d.jpg", animPath, i);
    if (!LittleFS.rename(tempName, finalName)) {
      Serial.printf("[瘦身] 最终重命名失败: %s -> %s\n", tempName, finalName);
      return false;
    }
  }

  Serial.printf("[瘦身] %s 完成，现有 %d 帧\n", animPath, kept);
  return true;
}

void thinAnimFolders() {
  Serial.println("\n[瘦身] 开始处理 /anim6 /anim7 /anim8 ...");
  unsigned long start = millis();
  const char* targets[] = {"/anim6", "/anim7", "/anim8"};
  int success = 0;
  int failed = 0;

  for (const char* animPath : targets) {
    if (!LittleFS.exists(animPath)) {
      Serial.printf("[瘦身] 目录不存在，跳过: %s\n", animPath);
      continue;
    }
    if (thinSingleAnimFolder(animPath)) {
      success++;
    } else {
      failed++;
    }
    yield();
  }

  Serial.println("\n========================================");
  Serial.printf("[瘦身] 完成: %d 成功, %d 失败\n", success, failed);
  Serial.printf("[瘦身] 用时: %.1f 秒\n", (millis() - start) / 1000.0);
  showStatus();
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
      case 'H': case 'h': thinAnimFolders(); break;
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
