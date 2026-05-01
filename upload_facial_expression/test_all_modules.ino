/*
 * XIAO ESP32S3 全模块综合测试程序
 * 
 * 接线：
 * - 屏幕：D0(CS), D1(DC), D2(RST), D8(SCK), D10(MOSI), BL->VCC
 * - STS3032舵机：D6(TX), D7(RX) - 波特率1000000
 * - PCA9685：D4(SDA), D5(SCL)
 * - MAX98357A：D11(BCLK), D12(LRC), D9(DIN)
 * 
 * 串口命令：
 * 1 - 测试屏幕（红绿蓝循环）
 * 2 - 测试PCA9685（0和1号舵机）
 * 3 - 测试音频（播放测试音）
 * 4 - 测试STS3032舵机（123号舵机）
 * 5 - 所有测试同时运行
 * 0 - 停止所有测试
 * 
 * 动画播放命令：
 * a1 - 播放动画1 (anim1, 自动检测帧数)
 * a2 - 播放动画2 (anim2, 自动检测帧数)
 * a3 - 播放动画3 (anim3, 自动检测帧数)
 * a0 - 停止动画播放
 * al - 循环播放当前动画
 * 
 * STS3032舵机专用命令：
 * s - 扫描所有舵机ID（范围1-253）
 * m<id> - 控制特定ID舵机转动（如：m1, m5, m10）
 * p<id> - 读取特定ID舵机位置（如：p1, p5）
 * 
 * 上传动画数据：
 * Arduino IDE: 工具 -> ESP32 Sketch Data Upload (需要安装LittleFS插件)
 * 或使用 arduino-cli 上传 data 文件夹
 */

#include <SPI.h>
#include <Adafruit_GFX.h>
#include <Adafruit_ST7789.h>
#include <Wire.h>
#include <Adafruit_PWMServoDriver.h>
#include <driver/i2s.h>
#include <SCServo.h>  // STS3032舵机库
#include <LittleFS.h>
#include <JPEGDEC.h>  // 需要安装 JPEGDEC 库

// ========== 引脚定义 ==========
// 屏幕
#define TFT_CS    D0
#define TFT_DC    D1
#define TFT_RST   D2
#define TFT_MOSI  D10
#define TFT_SCK   D8
#define TFT_W 170
#define TFT_H 320

// PCA9685 (I2C)
#define I2C_SDA   D4
#define I2C_SCL   D5

// MAX98357A (I2S)
#define I2S_BCLK  42   // D11
#define I2S_LRC   41   // D12
#define I2S_DOUT  8    // D9

// STS3032舵机 (UART)
#define STS_TX   43   // D6
#define STS_RX   44   // D7
#define STS_BAUD 1000000  // 1Mbps

// ========== 对象初始化 ==========
// 使用硬件SPI，速度更快
Adafruit_ST7789 tft = Adafruit_ST7789(TFT_CS, TFT_DC, TFT_RST);
Adafruit_PWMServoDriver pca = Adafruit_PWMServoDriver();
SMS_STS sts;  // STS3032舵机对象

// ========== 测试状态 ==========
int testMode = 0;  // 0=停止, 1=屏幕, 2=舵机, 3=音频, 4=STS3032, 5=全部
unsigned long lastUpdate = 0;
int currentColor = 0;
int servoPos = 0;
bool servoDirection = true;

// STS3032舵机控制状态
int foundServoIDs[254];      // 存储找到的舵机ID
int foundServoCount = 0;      // 找到的舵机数量
int singleServoID = -1;       // 当前控制的单个舵机ID
bool singleServoMode = false; // 单舵机控制模式

// ========== 动画播放状态 ==========
JPEGDEC jpeg;
int currentAnim = 0;          // 当前动画 (0=无, 1/2/3)
int currentFrame = 1;         // 当前帧号
bool animLoop = false;        // 是否循环播放
unsigned long lastFrameTime = 0;
const int FRAME_DELAY = 67;   // 约15fps (67ms每帧)

// 动画帧数配置 - 自动检测
int ANIM_FRAMES[] = {0, 0, 0, 0};  // 启动时自动扫描填充
const char* ANIM_PATHS[] = {"", "/anim1", "/anim2", "/anim3"};

// JPEG文件缓冲区 (使用PSRAM)
uint8_t *jpegBuffer = NULL;
const size_t JPEG_BUFFER_SIZE = 65536;  // 64KB缓冲区

// 帧缓冲区 (用于双缓冲，避免撕裂) - 170x320 RGB565 = 108800字节
uint16_t *frameBuffer = NULL;
const size_t FRAME_BUFFER_SIZE = TFT_W * TFT_H * 2;  // 170x320x2 bytes

// ========== 音频生成（正弦波） ==========
void generateTone(int frequency, int duration_ms) {
  const int sample_rate = 16000;
  const int samples = (sample_rate * duration_ms) / 1000;
  
  int16_t *samples_data = (int16_t *)malloc(samples * sizeof(int16_t));
  if (!samples_data) {
    Serial.println("内存分配失败");
    return;
  }
  
  // 生成正弦波
  for (int i = 0; i < samples; i++) {
    float t = (float)i / sample_rate;
    samples_data[i] = (int16_t)(sin(2.0 * PI * frequency * t) * 10000);
  }
  
  // 写入I2S
  size_t bytes_written;
  i2s_write(I2S_NUM_0, samples_data, samples * sizeof(int16_t), &bytes_written, portMAX_DELAY);
  
  free(samples_data);
}

// ========== 初始化I2S ==========
void setupI2S() {
  i2s_config_t i2s_config = {
    .mode = (i2s_mode_t)(I2S_MODE_MASTER | I2S_MODE_TX),
    .sample_rate = 16000,
    .bits_per_sample = I2S_BITS_PER_SAMPLE_16BIT,
    .channel_format = I2S_CHANNEL_FMT_ONLY_LEFT,
    .communication_format = I2S_COMM_FORMAT_STAND_I2S,
    .intr_alloc_flags = ESP_INTR_FLAG_LEVEL1,
    .dma_buf_count = 8,
    .dma_buf_len = 64,
    .use_apll = false,
    .tx_desc_auto_clear = true
  };
  
  i2s_pin_config_t pin_config = {
    .bck_io_num = I2S_BCLK,
    .ws_io_num = I2S_LRC,
    .data_out_num = I2S_DOUT,
    .data_in_num = I2S_PIN_NO_CHANGE
  };
  
  i2s_driver_install(I2S_NUM_0, &i2s_config, 0, NULL);
  i2s_set_pin(I2S_NUM_0, &pin_config);
  i2s_zero_dma_buffer(I2S_NUM_0);
}

// ========== JPEG绘制回调函数（使用帧缓冲）==========
int jpegDrawCallback(JPEGDRAW *pDraw) {
  // 将解码的像素块复制到帧缓冲区
  if (frameBuffer == NULL) {
    // 如果没有帧缓冲，直接绘制到屏幕
    tft.drawRGBBitmap(pDraw->x, pDraw->y, pDraw->pPixels, pDraw->iWidth, pDraw->iHeight);
  } else {
    // 复制到帧缓冲区
    uint16_t *src = pDraw->pPixels;
    for (int y = 0; y < pDraw->iHeight; y++) {
      int destY = pDraw->y + y;
      if (destY >= 0 && destY < TFT_H) {
        for (int x = 0; x < pDraw->iWidth; x++) {
          int destX = pDraw->x + x;
          if (destX >= 0 && destX < TFT_W) {
            frameBuffer[destY * TFT_W + destX] = src[y * pDraw->iWidth + x];
          }
        }
      }
    }
  }
  return 1;  // 继续解码
}

// ========== 显示JPEG图片 ==========
bool displayJPEG(const char* filename) {
  File file = LittleFS.open(filename, "r");
  if (!file) {
    return false;
  }
  
  size_t fileSize = file.size();
  if (fileSize > JPEG_BUFFER_SIZE) {
    file.close();
    return false;
  }
  
  // 读取文件到缓冲区
  size_t bytesRead = file.read(jpegBuffer, fileSize);
  file.close();
  
  if (bytesRead != fileSize) {
    return false;
  }
  
  // 解码JPEG到帧缓冲区
  if (jpeg.openRAM(jpegBuffer, fileSize, jpegDrawCallback)) {
    // 使用小端序 RGB565（ST7789通常需要这个）
    jpeg.setPixelType(RGB565_LITTLE_ENDIAN);
    jpeg.decode(0, 0, 0);  // 从(0,0)开始绘制，不缩放
    jpeg.close();
    
    // 一次性将帧缓冲区绘制到屏幕（避免撕裂）
    if (frameBuffer != NULL) {
      tft.drawRGBBitmap(0, 0, frameBuffer, TFT_W, TFT_H);
    }
    
    return true;
  }
  
  return false;
}

// ========== 播放动画帧 ==========
void playAnimationFrame() {
  if (currentAnim < 1 || currentAnim > 3) return;
  
  char filename[32];
  sprintf(filename, "%s/%04d.jpg", ANIM_PATHS[currentAnim], currentFrame);
  
  if (displayJPEG(filename)) {
    currentFrame++;
    
    // 检查是否播放完毕
    if (currentFrame > ANIM_FRAMES[currentAnim]) {
      if (animLoop) {
        currentFrame = 1;  // 循环播放
        Serial.println("动画循环重新开始");
      } else {
        Serial.println("✓ 动画播放完成");
        currentAnim = 0;
        currentFrame = 1;
      }
    }
  } else {
    // 文件读取失败，停止播放
    Serial.print("停止播放，帧 ");
    Serial.print(currentFrame);
    Serial.println(" 读取失败");
    currentAnim = 0;
  }
}

// ========== 开始播放动画 ==========
void startAnimation(int animNum) {
  if (animNum < 1 || animNum > 3) {
    Serial.println("❌ 无效的动画编号 (1-3)");
    return;
  }
  
  // 检查动画文件夹是否存在
  if (!LittleFS.exists(ANIM_PATHS[animNum])) {
    Serial.print("❌ 动画文件夹不存在: ");
    Serial.println(ANIM_PATHS[animNum]);
    Serial.println("   请先上传data文件夹到Flash");
    return;
  }
  
  currentAnim = animNum;
  currentFrame = 1;
  lastFrameTime = 0;
  testMode = 7;  // 动画播放模式
  
  Serial.println("\n========================================");
  Serial.print("开始播放动画 ");
  Serial.println(animNum);
  Serial.print("总帧数: ");
  Serial.println(ANIM_FRAMES[animNum]);
  Serial.print("循环: ");
  Serial.println(animLoop ? "是" : "否");
  Serial.println("按 'a0' 停止，'al' 切换循环");
  Serial.println("========================================\n");
}

// ========== 停止动画 ==========
void stopAnimation() {
  currentAnim = 0;
  currentFrame = 1;
  testMode = 0;
  tft.fillScreen(ST77XX_BLACK);
  tft.setCursor(10, 10);
  tft.setTextColor(ST77XX_WHITE);
  tft.setTextSize(2);
  tft.println("Anim Stopped");
  Serial.println("✓ 动画已停止");
}

// ========== 扫描动画帧数 ==========
int countAnimFrames(const char* animPath) {
  // 检测动画文件夹中有多少帧（格式：0001.jpg, 0002.jpg, ...）
  int count = 0;
  char filename[32];
  
  // 从1开始检测，直到找不到文件为止
  for (int i = 1; i <= 9999; i++) {
    sprintf(filename, "%s/%04d.jpg", animPath, i);
    if (LittleFS.exists(filename)) {
      count = i;  // 更新最大帧号
    } else {
      break;  // 遇到不存在的文件就停止
    }
  }
  
  return count;
}

// ========== 初始化LittleFS ==========
bool setupLittleFS() {
  Serial.println("初始化LittleFS...");
  
  if (!LittleFS.begin(true)) {  // true = 格式化如果挂载失败
    Serial.println("❌ LittleFS初始化失败!");
    return false;
  }
  
  // 显示存储信息
  size_t totalBytes = LittleFS.totalBytes();
  size_t usedBytes = LittleFS.usedBytes();
  
  Serial.print("  总空间: ");
  Serial.print(totalBytes / 1024);
  Serial.println(" KB");
  Serial.print("  已用: ");
  Serial.print(usedBytes / 1024);
  Serial.println(" KB");
  Serial.print("  可用: ");
  Serial.print((totalBytes - usedBytes) / 1024);
  Serial.println(" KB");
  
  // 自动扫描动画帧数
  Serial.println("  扫描动画文件:");
  for (int i = 1; i <= 3; i++) {
    if (LittleFS.exists(ANIM_PATHS[i])) {
      ANIM_FRAMES[i] = countAnimFrames(ANIM_PATHS[i]);
      Serial.print("    ✓ ");
      Serial.print(ANIM_PATHS[i]);
      Serial.print(" (");
      Serial.print(ANIM_FRAMES[i]);
      Serial.println(" 帧)");
    } else {
      ANIM_FRAMES[i] = 0;
      Serial.print("    ✗ ");
      Serial.print(ANIM_PATHS[i]);
      Serial.println(" (未找到)");
    }
  }
  
  Serial.println("✓ LittleFS初始化完成");
  return true;
}

// ========== 扫描所有STS3032舵机 ==========
void scanSTS3032() {
  Serial.println("\n========================================");
  Serial.println("开始扫描STS3032舵机 (ID: 1-253)...");
  Serial.println("========================================");
  
  foundServoCount = 0;
  
  for (int id = 1; id <= 253; id++) {
    // 显示扫描进度
    if (id % 50 == 0) {
      Serial.print("扫描进度: ");
      Serial.print(id);
      Serial.println("/253");
    }
    
    int result = sts.Ping(id);
    if (result != -1) {
      foundServoIDs[foundServoCount] = id;
      foundServoCount++;
      
      // 读取舵机当前位置
      int pos = sts.ReadPos(id);
      
      Serial.print("  ✓ 找到舵机 ID: ");
      Serial.print(id);
      Serial.print(" | 当前位置: ");
      Serial.println(pos);
    }
    delay(5);  // 短暂延时避免总线冲突
  }
  
  Serial.println("\n========================================");
  Serial.print("扫描完成！共找到 ");
  Serial.print(foundServoCount);
  Serial.println(" 个舵机");
  
  if (foundServoCount > 0) {
    Serial.print("舵机ID列表: ");
    for (int i = 0; i < foundServoCount; i++) {
      Serial.print(foundServoIDs[i]);
      if (i < foundServoCount - 1) Serial.print(", ");
    }
    Serial.println();
  }
  Serial.println("========================================\n");
}

// ========== 控制单个STS3032舵机 ==========
void moveSingleSTS3032(int id) {
  // 检查舵机是否存在
  if (sts.Ping(id) == -1) {
    Serial.print("❌ 未找到ID为 ");
    Serial.print(id);
    Serial.println(" 的舵机");
    return;
  }
  
  singleServoID = id;
  singleServoMode = true;
  testMode = 6;  // 使用新的测试模式
  lastUpdate = 0;
  servoDirection = true;
  
  // 启用扭矩
  sts.EnableTorque(id, 1);
  delay(10);
  
  Serial.println("\n========================================");
  Serial.print("开始控制舵机 ID: ");
  Serial.println(id);
  Serial.println("舵机将在位置500和2500之间往返");
  Serial.println("按 '0' 停止");
  Serial.println("========================================\n");
}

// ========== 测试6：单个STS3032舵机控制 ==========
void testSingleSTS3032() {
  if (millis() - lastUpdate > 2000) {
    lastUpdate = millis();
    
    int targetPos = servoDirection ? 500 : 2500;
    sts.WritePosEx(singleServoID, targetPos, 1000, 50);
    
    Serial.print("舵机 ID ");
    Serial.print(singleServoID);
    Serial.print(" -> 位置 ");
    Serial.println(targetPos);
    
    servoDirection = !servoDirection;
  }
}

// ========== 读取舵机位置 ==========
void readServoPosition(int id) {
  if (sts.Ping(id) == -1) {
    Serial.print("❌ 未找到ID为 ");
    Serial.print(id);
    Serial.println(" 的舵机");
    return;
  }
  
  int pos = sts.ReadPos(id);
  int speed = sts.ReadSpeed(id);
  int load = sts.ReadLoad(id);
  int voltage = sts.ReadVoltage(id);
  int temp = sts.ReadTemper(id);
  
  Serial.println("\n========================================");
  Serial.print("舵机 ID ");
  Serial.print(id);
  Serial.println(" 状态信息:");
  Serial.println("========================================");
  Serial.print("  位置: ");
  Serial.println(pos);
  Serial.print("  速度: ");
  Serial.println(speed);
  Serial.print("  负载: ");
  Serial.println(load);
  Serial.print("  电压: ");
  Serial.print(voltage / 10.0);
  Serial.println(" V");
  Serial.print("  温度: ");
  Serial.print(temp);
  Serial.println(" °C");
  Serial.println("========================================\n");
}

// ========== 测试1：屏幕颜色循环 ==========
void testScreen() {
  if (millis() - lastUpdate > 1000) {
    lastUpdate = millis();
    
    switch(currentColor) {
      case 0:
        tft.fillScreen(ST77XX_RED);
        Serial.println("屏幕: 红色");
        break;
      case 1:
        tft.fillScreen(ST77XX_GREEN);
        Serial.println("屏幕: 绿色");
        break;
      case 2:
        tft.fillScreen(ST77XX_BLUE);
        Serial.println("屏幕: 蓝色");
        break;
    }
    
    currentColor = (currentColor + 1) % 3;
  }
}

// ========== 测试2：PCA9685舵机 ==========
void testPCA9685() {
  if (millis() - lastUpdate > 1000) {
    lastUpdate = millis();
    
    if (servoDirection) {
      // 0号舵机到90度，1号舵机到0度
      int pulse0 = map(90, 0, 180, 150, 600);
      int pulse1 = map(0, 0, 180, 150, 600);
      pca.setPWM(0, 0, pulse0);
      pca.setPWM(1, 0, pulse1);
      Serial.println("PCA9685: 舵机0->90°, 舵机1->0°");
    } else {
      // 0号舵机到0度，1号舵机到90度
      int pulse0 = map(0, 0, 180, 150, 600);
      int pulse1 = map(90, 0, 180, 150, 600);
      pca.setPWM(0, 0, pulse0);
      pca.setPWM(1, 0, pulse1);
      Serial.println("PCA9685: 舵机0->0°, 舵机1->90°");
    }
    
    servoDirection = !servoDirection;
  }
}

// ========== 测试3：音频播放 ==========
void testAudio() {
  Serial.println("播放测试音: 440Hz (A音)");
  generateTone(440, 200);  // 440Hz, 200ms
  delay(300);
  
  Serial.println("播放测试音: 523Hz (C音)");
  generateTone(523, 200);  // 523Hz, 200ms
  delay(300);
  
  Serial.println("播放测试音: 659Hz (E音)");
  generateTone(659, 200);  // 659Hz, 200ms
  delay(800);
}

// ========== 测试4：STS3032舵机 ==========
void testSTS3032() {
  if (millis() - lastUpdate > 2000) {
    lastUpdate = millis();
    
    if (servoDirection) {
      // 三个舵机移动到500位置
      sts.WritePosEx(1, 500, 1000, 50);
      delay(20);
      sts.WritePosEx(2, 500, 1000, 50);
      delay(20);
      sts.WritePosEx(3, 500, 1000, 50);
      Serial.println("STS3032: 舵机1/2/3 -> 位置500");
    } else {
      // 三个舵机移动到2500位置
      sts.WritePosEx(1, 2500, 1000, 50);
      delay(20);
      sts.WritePosEx(2, 2500, 1000, 50);
      delay(20);
      sts.WritePosEx(3, 2500, 1000, 50);
      Serial.println("STS3032: 舵机1/2/3 -> 位置2500");
    }
    
    servoDirection = !servoDirection;
  }
}

// ========== 测试5：全部同时运行 ==========
void testAll() {
  static unsigned long lastScreenUpdate = 0;
  static unsigned long lastServoUpdate = 0;
  static unsigned long lastAudioUpdate = 0;
  static unsigned long lastURT1Update = 0;
  
  // 屏幕更新（每1秒）
  if (millis() - lastScreenUpdate > 1000) {
    lastScreenUpdate = millis();
    switch(currentColor) {
      case 0: tft.fillScreen(ST77XX_RED); break;
      case 1: tft.fillScreen(ST77XX_GREEN); break;
      case 2: tft.fillScreen(ST77XX_BLUE); break;
    }
    currentColor = (currentColor + 1) % 3;
  }
  
  // PCA9685更新（每1秒）
  if (millis() - lastServoUpdate > 1000) {
    lastServoUpdate = millis();
    if (servoDirection) {
      pca.setPWM(0, 0, map(90, 0, 180, 150, 600));
      pca.setPWM(1, 0, map(0, 0, 180, 150, 600));
    } else {
      pca.setPWM(0, 0, map(0, 0, 180, 150, 600));
      pca.setPWM(1, 0, map(90, 0, 180, 150, 600));
    }
    servoDirection = !servoDirection;
  }
  
  // 音频更新（每3秒）
  if (millis() - lastAudioUpdate > 3000) {
    lastAudioUpdate = millis();
    generateTone(440, 100);
  }
  
  // STS3032更新（每2秒）
  if (millis() - lastURT1Update > 2000) {
    lastURT1Update = millis();
    static bool stsDir = true;
    if (stsDir) {
      sts.WritePosEx(1, 500, 1000, 50);
      delay(20);
      sts.WritePosEx(2, 500, 1000, 50);
      delay(20);
      sts.WritePosEx(3, 500, 1000, 50);
    } else {
      sts.WritePosEx(1, 2500, 1000, 50);
      delay(20);
      sts.WritePosEx(2, 2500, 1000, 50);
      delay(20);
      sts.WritePosEx(3, 2500, 1000, 50);
    }
    stsDir = !stsDir;
  }
}

// ========== Setup ==========
void setup() {
  Serial.begin(115200);
  delay(1000);
  
  Serial.println("\n========================================");
  Serial.println("  XIAO ESP32S3 全模块测试程序");
  Serial.println("========================================");
  
  // 检查PSRAM
  Serial.print("PSRAM: ");
  if (psramFound()) {
    Serial.print(ESP.getPsramSize() / 1024 / 1024);
    Serial.println(" MB");
  } else {
    Serial.println("未检测到");
  }
  
  // 分配JPEG缓冲区（优先使用PSRAM）
  if (psramFound()) {
    jpegBuffer = (uint8_t *)ps_malloc(JPEG_BUFFER_SIZE);
    frameBuffer = (uint16_t *)ps_malloc(FRAME_BUFFER_SIZE);
  } else {
    jpegBuffer = (uint8_t *)malloc(JPEG_BUFFER_SIZE);
    frameBuffer = NULL;  // 内存不够时不使用帧缓冲
  }
  
  if (jpegBuffer) {
    Serial.println("✓ JPEG缓冲区分配成功");
  } else {
    Serial.println("❌ JPEG缓冲区分配失败!");
  }
  
  if (frameBuffer) {
    Serial.println("✓ 帧缓冲区分配成功 (双缓冲模式)");
  } else {
    Serial.println("⚠ 帧缓冲区未分配 (直接绘制模式)");
  }
  
  // 初始化屏幕（使用硬件SPI，高速模式）
  Serial.println("初始化屏幕...");
  SPI.begin(TFT_SCK, -1, TFT_MOSI, TFT_CS);  // SCK, MISO(不用), MOSI, CS
  tft.init(TFT_W, TFT_H);
  tft.setRotation(0);
  tft.setSPISpeed(80000000);  // 80MHz SPI速度，大幅提升刷新率
  tft.fillScreen(ST77XX_BLACK);
  tft.setCursor(10, 10);
  tft.setTextColor(ST77XX_WHITE);
  tft.setTextSize(2);
  tft.println("Test Ready");
  Serial.println("✓ 屏幕初始化完成 (80MHz SPI)");
  
  // 初始化LittleFS
  setupLittleFS();
  
  // 初始化I2C和PCA9685
  Serial.println("初始化PCA9685...");
  Wire.begin(I2C_SDA, I2C_SCL);
  pca.begin();
  pca.setPWMFreq(50);  // 50Hz for servos
  Serial.println("✓ PCA9685初始化完成");
  
  // 初始化I2S音频
  Serial.println("初始化I2S音频...");
  setupI2S();
  Serial.println("✓ I2S音频初始化完成");
  
  // 初始化STS3032舵机
  Serial.println("初始化STS3032舵机...");
  Serial1.begin(STS_BAUD, SERIAL_8N1, STS_RX, STS_TX);
  delay(100);
  sts.pSerial = &Serial1;
  
  // 测试舵机连接
  bool servoFound = false;
  for (int id = 1; id <= 3; id++) {
    if (sts.Ping(id) != -1) {
      Serial.print("  ✓ 找到舵机ID: ");
      Serial.println(id);
      servoFound = true;
      // 启用扭矩
      sts.EnableTorque(id, 1);
      delay(10);
    }
  }
  
  if (servoFound) {
    Serial.println("✓ STS3032舵机初始化完成");
  } else {
    Serial.println("⚠️  未找到STS3032舵机 (测试4将跳过)");
  }
  
  Serial.println("\n========================================");
  Serial.println("所有模块初始化完成！");
  Serial.println("========================================");
  Serial.println("测试命令：");
  Serial.println("  1 - 测试屏幕（红绿蓝循环）");
  Serial.println("  2 - 测试PCA9685（0和1号舵机）");
  Serial.println("  3 - 测试音频（播放测试音）");
  Serial.println("  4 - 测试STS3032舵机（123号舵机）");
  Serial.println("  5 - 全部同时测试");
  Serial.println("  0 - 停止测试");
  Serial.println("");
  Serial.println("动画播放命令：");
  Serial.printf("  a1 - 播放动画1 (%d帧)\n", ANIM_FRAMES[1]);
  Serial.printf("  a2 - 播放动画2 (%d帧)\n", ANIM_FRAMES[2]);
  Serial.printf("  a3 - 播放动画3 (%d帧)\n", ANIM_FRAMES[3]);
  Serial.println("  a0 - 停止动画");
  Serial.println("  al - 切换循环模式");
  Serial.println("");
  Serial.println("STS3032舵机专用命令：");
  Serial.println("  s - 扫描所有舵机ID");
  Serial.println("  m<id> - 控制特定ID舵机（如: m1, m5）");
  Serial.println("  p<id> - 读取特定ID舵机位置（如: p1）");
  Serial.println("");
  Serial.println("存储管理：");
  Serial.println("  F - 格式化Flash（清空所有文件）");
  Serial.println("========================================\n");
  
  tft.fillScreen(ST77XX_GREEN);
  delay(500);
  tft.fillScreen(ST77XX_BLACK);
}

// ========== Loop ==========
void loop() {
  // 检查串口命令
  if (Serial.available() > 0) {
    char cmd = Serial.read();
    
    // 处理数字命令 0-5
    if (cmd >= '0' && cmd <= '5') {
      testMode = cmd - '0';
      lastUpdate = 0;
      currentColor = 0;
      servoDirection = true;
      singleServoMode = false;
      
      Serial.print("\n>>> 切换到测试模式: ");
      Serial.println(testMode);
      
      switch(testMode) {
        case 0:
          Serial.println("停止所有测试");
          tft.fillScreen(ST77XX_BLACK);
          tft.setCursor(10, 10);
          tft.setTextColor(ST77XX_WHITE);
          tft.setTextSize(2);
          tft.println("Stopped");
          break;
        case 1:
          Serial.println("测试模式1: 屏幕颜色循环");
          break;
        case 2:
          Serial.println("测试模式2: PCA9685舵机");
          break;
        case 3:
          Serial.println("测试模式3: 音频播放");
          break;
        case 4:
          Serial.println("测试模式4: STS3032舵机");
          break;
        case 5:
          Serial.println("测试模式5: 全部同时运行");
          break;
      }
      Serial.println();
    }
    // 处理动画命令 'a' 或 'A'
    else if (cmd == 'a' || cmd == 'A') {
      delay(50);
      if (Serial.available() > 0) {
        char subCmd = Serial.read();
        if (subCmd == '1') {
          startAnimation(1);
        } else if (subCmd == '2') {
          startAnimation(2);
        } else if (subCmd == '3') {
          startAnimation(3);
        } else if (subCmd == '0') {
          stopAnimation();
        } else if (subCmd == 'l' || subCmd == 'L') {
          animLoop = !animLoop;
          Serial.print("循环模式: ");
          Serial.println(animLoop ? "开启" : "关闭");
        } else {
          Serial.println("动画命令: a1/a2/a3(播放) a0(停止) al(循环)");
        }
      } else {
        Serial.println("动画命令: a1/a2/a3(播放) a0(停止) al(循环)");
      }
    }
    // 处理扫描命令 's' 或 'S'
    else if (cmd == 's' || cmd == 'S') {
      testMode = 0;  // 停止当前测试
      scanSTS3032();
    }
    // 处理移动命令 'm<id>' 或 'M<id>'
    else if (cmd == 'm' || cmd == 'M') {
      delay(50);  // 等待ID数字输入
      String idStr = "";
      while (Serial.available() > 0) {
        char c = Serial.read();
        if (c >= '0' && c <= '9') {
          idStr += c;
        }
        delay(5);
      }
      
      if (idStr.length() > 0) {
        int id = idStr.toInt();
        if (id >= 1 && id <= 253) {
          moveSingleSTS3032(id);
        } else {
          Serial.println("❌ 无效的舵机ID (有效范围: 1-253)");
        }
      } else {
        Serial.println("❌ 请输入舵机ID，如: m1, m5, m10");
      }
    }
    // 处理读取位置命令 'p<id>' 或 'P<id>'
    else if (cmd == 'p' || cmd == 'P') {
      delay(50);  // 等待ID数字输入
      String idStr = "";
      while (Serial.available() > 0) {
        char c = Serial.read();
        if (c >= '0' && c <= '9') {
          idStr += c;
        }
        delay(5);
      }
      
      if (idStr.length() > 0) {
        int id = idStr.toInt();
        if (id >= 1 && id <= 253) {
          readServoPosition(id);
        } else {
          Serial.println("❌ 无效的舵机ID (有效范围: 1-253)");
        }
      } else {
        Serial.println("❌ 请输入舵机ID，如: p1, p5, p10");
      }
    }
    // 处理格式化命令 'F'
    else if (cmd == 'F') {
      Serial.println("\n========================================");
      Serial.println("⚠️  警告：即将格式化Flash，删除所有文件！");
      Serial.println("按 'Y' 确认，其他键取消");
      Serial.println("========================================");
      
      // 等待确认
      unsigned long timeout = millis() + 10000;
      while (!Serial.available() && millis() < timeout) {
        delay(100);
      }
      
      if (Serial.available()) {
        char confirm = Serial.read();
        if (confirm == 'Y' || confirm == 'y') {
          Serial.println("正在格式化...");
          LittleFS.format();
          LittleFS.begin();
          Serial.println("✓ 格式化完成！");
          Serial.printf("可用空间: %d KB\n", LittleFS.totalBytes() / 1024);
        } else {
          Serial.println("已取消");
        }
      } else {
        Serial.println("超时，已取消");
      }
      Serial.println();
    }
    // 处理帮助命令 'h' 或 'H' 或 '?'
    else if (cmd == 'h' || cmd == 'H' || cmd == '?') {
      Serial.println("\n========================================");
      Serial.println("帮助 - 可用命令列表：");
      Serial.println("========================================");
      Serial.println("测试命令：");
      Serial.println("  1 - 测试屏幕（红绿蓝循环）");
      Serial.println("  2 - 测试PCA9685（0和1号舵机）");
      Serial.println("  3 - 测试音频（播放测试音）");
      Serial.println("  4 - 测试STS3032舵机（123号舵机）");
      Serial.println("  5 - 全部同时测试");
      Serial.println("  0 - 停止测试");
      Serial.println("");
      Serial.println("动画播放命令：");
      Serial.printf("  a1 - 播放动画1 (%d帧)\n", ANIM_FRAMES[1]);
      Serial.printf("  a2 - 播放动画2 (%d帧)\n", ANIM_FRAMES[2]);
      Serial.printf("  a3 - 播放动画3 (%d帧)\n", ANIM_FRAMES[3]);
      Serial.println("  a0 - 停止动画");
      Serial.println("  al - 切换循环模式");
      Serial.println("");
      Serial.println("STS3032舵机专用命令：");
      Serial.println("  s     - 扫描所有舵机ID (1-253)");
      Serial.println("  m<id> - 控制特定ID舵机转动");
      Serial.println("          例: m1, m5, m10");
      Serial.println("  p<id> - 读取特定ID舵机状态");
      Serial.println("          例: p1, p5, p10");
      Serial.println("  h/?   - 显示此帮助信息");
      Serial.println("========================================\n");
    }
  }
  
  // 执行对应的测试
  switch(testMode) {
    case 1:
      testScreen();
      break;
    case 2:
      testPCA9685();
      break;
    case 3:
      testAudio();
      testMode = 0;  // 音频播放一次后停止
      break;
    case 4:
      testSTS3032();
      break;
    case 5:
      testAll();
      break;
    case 6:
      // 单舵机控制模式
      if (singleServoMode && singleServoID > 0) {
        testSingleSTS3032();
      }
      break;
    case 7:
      // 动画播放模式
      if (currentAnim > 0 && millis() - lastFrameTime >= FRAME_DELAY) {
        lastFrameTime = millis();
        playAnimationFrame();
        yield();  // 让出CPU时间给其他任务（WiFi、蓝牙等）
      }
      break;
    case 0:
    default:
      // 空闲
      delay(100);
      break;
  }
  
  delay(10);
}



