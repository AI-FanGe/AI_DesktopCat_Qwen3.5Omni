#include <Adafruit_GFX.h>
#include <Adafruit_ST7789.h>
#include <ArduinoWebsockets.h>
#include <ESP_I2S.h>
#include <ESP32Servo.h>
#include <HTTPClient.h>
#include <JPEGDEC.h>
#include <LittleFS.h>
#include <SPI.h>
#include <WiFi.h>
#include <cstring>
#include <esp_camera.h>

// 串口日志总开关。播放 TTS 期间，Serial.print 在 USB-CDC / UART 上是同步写入，
// 即便是 115200 波特率下一行几十字节也可能阻塞 task 1-5ms，足以让 I2S 的 DMA
// buffer 出现 underrun，表现为"语音被其它事件冲着播卡顿"。默认直接关成 no-op；
// 需要本地调试时把 0 改回 1 即可恢复所有日志。
#define SERIAL_LOG_ENABLED 0
#if SERIAL_LOG_ENABLED
  #define LOGF(fmt, ...)  Serial.printf(fmt, ##__VA_ARGS__)
  #define LOGLN(...)      Serial.println(__VA_ARGS__)
#else
  // 关闭时也把所有参数引用一次，避免 GCC 对被日志"消费"的临时变量报
  // unused-but-set-variable / unused-parameter 警告。if (false) 块会被
  // 编译器彻底 dead-code 消除，运行时零开销。
  #define LOGF(fmt, ...)  do { if (false) { (void)Serial.printf(fmt, ##__VA_ARGS__); } } while (0)
  #define LOGLN(...)      do { if (false) { Serial.println(__VA_ARGS__); } } while (0)
#endif

using namespace websockets;

namespace {
constexpr const char* WIFI_SSID = "YOUR_WIFI_SSID";
constexpr const char* WIFI_PASS = "YOUR_WIFI_PASSWORD";
constexpr const char* SERVER_HOST = "192.168.1.113";
constexpr uint16_t SERVER_PORT = 8081;
constexpr const char* CAM_WS_PATH = "/ws/camera";
constexpr const char* AUD_WS_PATH = "/ws_audio";

constexpr int SERVO_COUNT = 4;
constexpr int SERVO_PINS[SERVO_COUNT] = {8, 43, 44, 4};
constexpr int SERVO_TRIM[SERVO_COUNT] = {0, 0, 0, 0};
constexpr bool SERVO_INVERT[SERVO_COUNT] = {false, false, false, false};
constexpr int SERVO_MIN_US = 500;
constexpr int SERVO_MAX_US = 2500;

// 舵机逻辑映射按 pre/旧版固件接线保持一致：
//   servo1 / CH0 -> 头部俯仰
//   servo2 / CH1 -> 左耳
//   servo3 / CH2 -> 右耳
//   servo4 / CH3 -> 头部左右
constexpr int HEAD_PITCH_INDEX = 0;  // servo1 / CH0
constexpr int LEFT_EAR_INDEX = 1;    // servo2 / CH1
constexpr int RIGHT_EAR_INDEX = 2;   // servo3 / CH2
constexpr int HEAD_YAW_INDEX = 3;    // servo4 / CH3
constexpr int HEAD_PITCH_MIN = 70;
constexpr int HEAD_PITCH_MAX = 96;
constexpr int HEAD_YAW_MIN = 50;
constexpr int HEAD_YAW_MAX = 130;
constexpr int EAR_LOGICAL_MIN = HEAD_YAW_MIN;
constexpr int EAR_LOGICAL_MAX = HEAD_YAW_MAX;

constexpr int TFT_CS = 2;
constexpr int TFT_DC = 3;
constexpr int TFT_RST = -1;
constexpr int TFT_SCLK = 7;
constexpr int TFT_MOSI = 9;
constexpr int TFT_W = 240;
constexpr int TFT_H = 284;
constexpr int TFT_COL_OFFSET = 0;
constexpr int TFT_ROW_OFFSET = 36;
constexpr uint32_t TFT_SPI_SPEED = 80000000;
// Server 端图文排版页先按 284x240 横屏排版，再整体 CCW 90° 旋到 240x284。
// 横屏预览框是 (24,24,132,92)，旋转后落在 TFT 坐标 (24,128,92,132)。
constexpr int VISION_LOCAL_PREVIEW_X = 24;
constexpr int VISION_LOCAL_PREVIEW_Y = 128;
constexpr int VISION_LOCAL_PREVIEW_W = 92;
constexpr int VISION_LOCAL_PREVIEW_H = 132;
constexpr int VISION_LOCAL_PREVIEW_FPS = 15;

constexpr int I2S_SPK_DIN = 1;
constexpr int I2S_SPK_BCLK = 6;
constexpr int I2S_SPK_LRCK = 5;
constexpr int I2S_MIC_CLOCK_PIN = 42;
constexpr int I2S_MIC_DATA_PIN = 41;
constexpr int TTS_RATE = 16000;
constexpr int SAMPLE_RATE = 16000;
constexpr int CHUNK_MS = 20;
constexpr int BYTES_PER_CHUNK = SAMPLE_RATE * CHUNK_MS / 1000 * 2;

constexpr framesize_t CAMERA_FRAME_SIZE = FRAMESIZE_VGA;
constexpr int JPEG_QUALITY = 14;
constexpr int CAMERA_FB_COUNT = 2;

constexpr int CAM_PIN_PWDN = -1;
constexpr int CAM_PIN_RESET = -1;
constexpr int CAM_PIN_XCLK = 10;
constexpr int CAM_PIN_SIOD = 40;
constexpr int CAM_PIN_SIOC = 39;
constexpr int CAM_PIN_D7 = 48;
constexpr int CAM_PIN_D6 = 11;
constexpr int CAM_PIN_D5 = 12;
constexpr int CAM_PIN_D4 = 14;
constexpr int CAM_PIN_D3 = 16;
constexpr int CAM_PIN_D2 = 18;
constexpr int CAM_PIN_D1 = 17;
constexpr int CAM_PIN_D0 = 15;
constexpr int CAM_PIN_VSYNC = 38;
constexpr int CAM_PIN_HREF = 47;
constexpr int CAM_PIN_PCLK = 13;

constexpr size_t JPEG_BUFFER_SIZE = 128 * 1024;
constexpr int CATPOSE_QUEUE_SIZE = 16;
constexpr int MAX_ANIMS = 8;
constexpr int MAX_MOUTH_ANIMS = 4;
constexpr int MAX_MOUTH_FRAMES = 15;
constexpr int FRAME_DELAY_MS = 20;
constexpr uint32_t CROSSFADE_DURATION_MS = 500;
constexpr bool LOCAL_EXPRESSION_ANIMATION_ENABLED = true;
constexpr bool LOCAL_ANIM_ROTATE_CCW_90 = true;
constexpr float LOCAL_ANIM_SCALE_X = 1.10f;  // 240 direction
constexpr float LOCAL_ANIM_SCALE_Y = 1.20f;  // 284 direction
constexpr int DEFAULT_CAMERA_FPS = 1;
constexpr const char* ANIM_PATH_PREFIX = "/anim";
constexpr const char* MOUTH_SMALL_PATH = "/mouth_small_open";
constexpr const char* MOUTH_BIG_PATH = "/mouth_big_open";
constexpr const char* MOUTH_WIDE_PATH = "/mouth_wide";
constexpr const char* MOUTH_ROUND_PATH = "/mouth_round";
constexpr const char* MOUTH_MASK_PATH = "/mouth_mask.jpg";
constexpr uint8_t MOUTH_MASK_THRESHOLD = 96;
constexpr int MOUTH_MASK_FEATHER_RADIUS = 2;
constexpr uint32_t WIFI_RETRY_INTERVAL_MS = 15000;
constexpr int COVER_DETECTION_FPS = 2;
constexpr size_t COVER_JPEG_SIZE_THRESHOLD = 14000;
constexpr uint8_t COVER_STABLE_FRAMES = 3;
constexpr uint32_t COVER_TOGGLE_DEBOUNCE_MS = 1200;
constexpr uint8_t BUILTIN_EXPR_ACTION_COUNT = 10;
constexpr uint8_t BUILTIN_IDLE_ACTION_COUNT = 10;
constexpr uint8_t BUILTIN_ACTION_COUNT = BUILTIN_EXPR_ACTION_COUNT + BUILTIN_IDLE_ACTION_COUNT;
constexpr uint32_t IDLE_ACTION_START_DELAY_MS = 4500;
constexpr uint32_t IDLE_STATIC_MIN_MS = 1600;
constexpr uint32_t IDLE_STATIC_MAX_MS = 3200;
constexpr uint32_t IDLE_ACTION_GAP_MIN_MS = 900;
constexpr uint32_t IDLE_ACTION_GAP_MAX_MS = 1800;
// 舵机错峰：四路 PWM 更新之间插入几百微秒，避免同一瞬间同时启动
// 拉出叠加电流造成欠压复位（brownout）。数值很小，动作看不出时序差。
constexpr uint16_t SERVO_STAGGER_US = 140;
// 进一步降低舵机最大速度：在原业务时长基础上统一放慢，并抬高最短动作时长。
// 这样说话/表情/待机动作都会缓和一些，同时更不容易在大角度折返时丢步。
constexpr uint8_t SERVO_DURATION_SCALE_NUM = 8;
constexpr uint8_t SERVO_DURATION_SCALE_DEN = 5;
constexpr uint16_t SERVO_MIN_MOVE_MS = 110;
// 实时 SERVO 指令（例如手势跟随）改为在 loop 中轻量限速追目标，
// 不增加任务数量和插值步数，只做几个整数运算即可换来更好的丝滑度。
constexpr uint32_t DIRECT_SERVO_UPDATE_INTERVAL_MS = 24;
constexpr uint8_t DIRECT_SERVO_MAX_STEP_DEG = 2;
constexpr uint8_t DIRECT_EAR_MAX_STEP_DEG = 3;
// TTS 播放时临时降低 WiFi 发射功率（dBm），避免 I2S 放大器 + WiFi TX 峰值叠加
constexpr int8_t WIFI_TX_POWER_NORMAL_DBM = 19;   // ~WIFI_POWER_19dBm
constexpr int8_t WIFI_TX_POWER_TTS_DBM = 11;      // ~WIFI_POWER_11dBm
}

class WaveshareST7789 : public Adafruit_ST7789 {
 public:
  WaveshareST7789(int8_t cs, int8_t dc, int8_t rst) : Adafruit_ST7789(cs, dc, rst) {}

  void applyOffsets(int8_t col, int8_t row) {
    setColRowStart(col, row);
  }

  // 保留 applyRotation 作为 setTftRotationIfNeeded 的统一入口；内部直接复用
  // Adafruit_ST7789 自带的 rotation 行为。rotation 0 经 initScreen 中的 applyOffsets
  // 已经获得正确的 (0, 36) 偏移，rotation 1/2/3 目前只在全屏 drawRGBBitmap 场景下使用，
  // 不依赖 rotation 2/3 的子区域 offset。
  void applyRotation(uint8_t rotation) {
    rotation &= 3;
    setRotation(rotation);
  }
};

// Adafruit_GFX 的 GFXcanvas16 会自己 malloc 一块 DRAM 作为画布，对 ESP32-S3 这种
// DRAM 紧张的平台不合适。这里继承 Adafruit_GFX，用外部传入的 PSRAM buffer 当画布，
// 配合 print/fillRect/drawRoundRect 等 GFX 原语，就能在离屏缓冲上绘制文字/图形。
class ExternalBufferCanvas : public Adafruit_GFX {
 public:
  ExternalBufferCanvas(int16_t w, int16_t h, uint16_t* buf)
      : Adafruit_GFX(w, h), _buf(buf) {}

  void drawPixel(int16_t x, int16_t y, uint16_t color) override {
    if (x < 0 || x >= _width || y < 0 || y >= _height) return;
    _buf[static_cast<int32_t>(y) * _width + x] = color;
  }

  void fillScreen(uint16_t color) override {
    int32_t n = static_cast<int32_t>(_width) * _height;
    for (int32_t i = 0; i < n; ++i) _buf[i] = color;
  }

 private:
  uint16_t* _buf;
};

struct AudioChunk {
  size_t n;
  uint8_t data[BYTES_PER_CHUNK];
};

struct CatPoseFrame {
  int yaw;
  int pitch;
  int ear;
  int durationMs;
};

struct BuiltinActionDef {
  const char* name;
  bool idleAction;
};

enum ScreenPageMode {
  SCREEN_PAGE_EXPRESSION = 0,
  SCREEN_PAGE_DASHBOARD = 1,
  SCREEN_PAGE_HOST_CAMERA = 2,
  SCREEN_PAGE_TRANSLATE = 3,
  SCREEN_PAGE_VISION_DIALOG = 4
};

Servo servos[SERVO_COUNT];
int currentServoAngles[SERVO_COUNT] = {90, 90, 90, 90};
int logicalEarAngle = 90;
bool rawPwmTestMode = false;

WaveshareST7789 tft(TFT_CS, TFT_DC, TFT_RST);
JPEGDEC jpeg;
I2SClass i2sIn;
I2SClass i2sOut;
WebsocketsClient wsCam;
WebsocketsClient wsAud;
WiFiClient ttsClient;

typedef camera_fb_t* fb_ptr_t;
QueueHandle_t qFrames = nullptr;
QueueHandle_t qAudio = nullptr;
QueueHandle_t qCatPoses = nullptr;
SemaphoreHandle_t jpegMutex = nullptr;

volatile bool camWsReady = false;
volatile bool audWsReady = false;
volatile bool runAudioStream = false;
volatile bool micEnabled = true;
volatile bool ttsAudioAvailable = false;
volatile bool ttsPlaying = false;
volatile int gTargetFps = DEFAULT_CAMERA_FPS;
volatile ScreenPageMode screenPageMode = SCREEN_PAGE_EXPRESSION;

uint8_t* jpegBuffer = nullptr;
uint16_t* frameBuffer = nullptr;
uint16_t* visionPreviewBuffer = nullptr;
uint16_t* visionPreviewDecodeBuffer = nullptr;
uint16_t* crossfadeOldBuf = nullptr;
uint16_t* crossfadeNewBuf = nullptr;
uint16_t* animDecodeBuffer = nullptr;
uint16_t* mouthFrameBuffer = nullptr;
uint16_t* mouthMaskBuffer = nullptr;
uint8_t* mouthMaskAlpha = nullptr;
uint16_t* jpegDecodeTarget = nullptr;
bool jpegDecodeRotateCCW90 = false;
int16_t animScaleXMap[TFT_W] = {0};
int16_t animScaleYMap[TFT_H] = {0};
bool mouthAssetsReady = false;
bool mouthTalkingMode = false;
bool mouthVisible = false;
uint8_t mouthCurrentType = 0;
float mouthEnergySmoothed = 0.0f;
float mouthBrightnessSmoothed = 0.0f;
float mouthFrameCursor = 0.0f;
float mouthPlaybackStep = 0.8f;
bool visionPreviewDecodeActive = false;
bool visionPreviewValid = false;
uint16_t* visionPreviewDecodeTarget = nullptr;
int visionPreviewCropX = 0;
int visionPreviewCropY = 0;
int visionPreviewCropW = 0;
int visionPreviewCropH = 0;
String currentEmotion = "neutral";
bool expressionBlink = false;
bool expressionDirty = true;
uint32_t lastExpressionRefreshMs = 0;
uint32_t lastCamRetryMs = 0;
uint32_t lastAudRetryMs = 0;
uint32_t lastWiFiRetryMs = 0;
wl_status_t lastWiFiStatus = WL_IDLE_STATUS;
volatile uint8_t speakerVolumePercent = 78;
bool coverInfoMode = false;
bool cameraCoverStable = false;
bool coverToggleArmed = true;
uint8_t coverDetectedFrames = 0;
uint8_t coverClearFrames = 0;
uint32_t lastCoverToggleMs = 0;
uint8_t currentTftRotation = 0;
volatile bool poseMotionActive = false;
volatile bool poseAbortRequested = false;
volatile bool directPoseActive = false;
volatile int directTargetYaw = 90;
volatile int directTargetPitch = 90;
volatile int directTargetEar = 90;
uint32_t lastInteractionMs = 0;
uint32_t nextIdleActionMs = 0;
uint32_t lastDirectServoUpdateMs = 0;
int currentAnim = 0;
int currentFrame = 1;
bool animLoop = false;
bool crossfadeActive = false;
uint32_t crossfadeStartMs = 0;
uint32_t lastFrameTime = 0;
int maxAnimIndex = 0;
int animFrames[MAX_ANIMS + 1] = {0};
char animPaths[MAX_ANIMS + 1][16] = {{0}};
bool expressionAnimNotifyPending = false;
String expressionAnimNotifyToken = "";
int mouthFrames[MAX_MOUTH_ANIMS] = {0};
char mouthPaths[MAX_MOUTH_ANIMS][24] = {{0}};

int countAnimFrames(const char* animPath);
bool displayJPEG(const char* filename, uint16_t* targetBuffer, bool drawIfBuffered, bool rotateCCW90);
bool isAnimAvailable(int animId);
void startIdleAnimationLoop();
void centerCatPose();
void clearPoseQueue(bool abortCurrent);
void noteInteraction();

bool lockJpegDecoder(TickType_t waitTicks = pdMS_TO_TICKS(1200)) {
  return jpegMutex == nullptr || xSemaphoreTake(jpegMutex, waitTicks) == pdTRUE;
}

void unlockJpegDecoder() {
  if (jpegMutex != nullptr) {
    xSemaphoreGive(jpegMutex);
  }
}

int clampAngle(int angle) {
  return constrain(angle, 0, 180);
}

int clampEarLogicalAngle(int angle) {
  return constrain(clampAngle(angle), EAR_LOGICAL_MIN, EAR_LOGICAL_MAX);
}

int clampServoLogicalAngle(int index, int angle) {
  angle = clampAngle(angle);
  if (index == HEAD_PITCH_INDEX) {
    return constrain(angle, HEAD_PITCH_MIN, HEAD_PITCH_MAX);
  }
  if (index == HEAD_YAW_INDEX) {
    return constrain(angle, HEAD_YAW_MIN, HEAD_YAW_MAX);
  }
  return angle;
}

int logicalToPhysicalAngle(int index, int logicalAngle) {
  int angle = clampServoLogicalAngle(index, logicalAngle);
  angle += SERVO_TRIM[index];
  angle = constrain(angle, 0, 180);
  if (SERVO_INVERT[index]) {
    angle = 180 - angle;
  }
  return angle;
}

void writeServoLogical(int index, int logicalAngle) {
  logicalAngle = clampServoLogicalAngle(index, logicalAngle);
  servos[index].write(logicalToPhysicalAngle(index, logicalAngle));
  currentServoAngles[index] = logicalAngle;
}

void writeMirroredEars(int logicalAngle) {
  logicalEarAngle = clampEarLogicalAngle(logicalAngle);
  writeServoLogical(LEFT_EAR_INDEX, logicalEarAngle);
  // 左右耳之间加微秒级错峰，避免两只耳朵舵机同一刻启动产生叠加峰值电流
  delayMicroseconds(SERVO_STAGGER_US);
  writeServoLogical(RIGHT_EAR_INDEX, 180 - logicalEarAngle);
}

int clampRawServoAngle(int angle) {
  return constrain(angle, 0, 180);
}

void writeServoRawPhysical(int index, int angle) {
  if (index < 0 || index >= SERVO_COUNT) return;
  int safeAngle = clampRawServoAngle(angle);
  servos[index].write(safeAngle);
  currentServoAngles[index] = safeAngle;
  if (index == LEFT_EAR_INDEX || index == RIGHT_EAR_INDEX) {
    logicalEarAngle = safeAngle;
  }
}

void setRawPwmTestMode(bool enabled, bool recenterPose = false) {
  rawPwmTestMode = enabled;
  clearPoseQueue(true);
  directPoseActive = false;
  poseAbortRequested = false;
  if (enabled) {
    noteInteraction();
    expressionDirty = true;
    return;
  }
  if (recenterPose) {
    centerCatPose();
  }
  noteInteraction();
  expressionDirty = true;
}

void applyCatPose(int yaw, int pitch, int ear) {
  // 四路舵机错峰写角度：每路之间 ~140μs，总体仅多出 ~420μs，肉眼/听觉完全感知不到，
  // 但能显著降低四路同时启动导致的瞬态电流叠加，从而缓解电池带载欠压重启。
  writeServoLogical(HEAD_YAW_INDEX, yaw);
  delayMicroseconds(SERVO_STAGGER_US);
  writeServoLogical(HEAD_PITCH_INDEX, pitch);
  delayMicroseconds(SERVO_STAGGER_US);
  writeMirroredEars(ear);
}

void centerCatPose() {
  applyCatPose(90, 90, 90);
}

void queueCatPose(int yaw, int pitch, int ear, int durationMs) {
  if (!qCatPoses) return;
  CatPoseFrame frame;
  frame.yaw = clampServoLogicalAngle(HEAD_YAW_INDEX, yaw);
  frame.pitch = clampServoLogicalAngle(HEAD_PITCH_INDEX, pitch);
  frame.ear = clampEarLogicalAngle(ear);
  frame.durationMs = constrain(durationMs, 60, 3000);
  xQueueSend(qCatPoses, &frame, 0);
}

void clearPoseQueue(bool abortCurrent = true) {
  poseAbortRequested = abortCurrent && poseMotionActive;
  directPoseActive = false;
  if (qCatPoses) xQueueReset(qCatPoses);
}

void noteInteraction() {
  uint32_t now = millis();
  lastInteractionMs = now;
  nextIdleActionMs = now + IDLE_ACTION_START_DELAY_MS;
}

const BuiltinActionDef BUILTIN_ACTIONS[BUILTIN_ACTION_COUNT] = {
    {"happy bounce", false},   {"sad droop", false},       {"angry stare", false},      {"shy hide", false},
    {"fear recoil", false},    {"thinking scan", false},   {"listening focus", false},  {"surprised pop", false},
    {"boring slump", false},   {"speechless freeze", false},
    {"idle breathe", true},    {"idle left", true},        {"idle right", true},        {"idle ear flick", true},
    {"idle peek up", true},    {"idle sway", true},        {"idle double tilt", true},  {"idle scan", true},
    {"idle nod", true},        {"idle curious", true},
};

const BuiltinActionDef* getBuiltinAction(uint8_t actionId) {
  if (actionId < 1 || actionId > BUILTIN_ACTION_COUNT) return nullptr;
  return &BUILTIN_ACTIONS[actionId - 1];
}

uint32_t queueBuiltinAction(uint8_t actionId, bool clearFirst = true) {
  const BuiltinActionDef* action = getBuiltinAction(actionId);
  if (action == nullptr) return 0;
  if (clearFirst) clearPoseQueue(true);

  auto randRange = [&](int minValue, int maxValue) -> int {
    if (minValue > maxValue) {
      int temp = minValue;
      minValue = maxValue;
      maxValue = temp;
    }
    return random(minValue, maxValue + 1);
  };

  auto scaledDuration = [&](int baseMs, int jitterMs) -> int {
    int duration = baseMs + randRange(-jitterMs, jitterMs);
    duration *= 2;
    return constrain(duration, 120, 3000);
  };

  auto addPose = [&](int yaw, int pitch, int ear, int baseMs, int jitterMs = 40) -> uint32_t {
    int duration = scaledDuration(baseMs, jitterMs);
    queueCatPose(yaw, pitch, ear, duration);
    return static_cast<uint32_t>(duration);
  };

  auto currentYaw = [&]() -> int { return currentServoAngles[HEAD_YAW_INDEX]; };
  auto currentPitch = [&]() -> int { return currentServoAngles[HEAD_PITCH_INDEX]; };
  auto currentEar = [&]() -> int { return logicalEarAngle; };
  auto yawAt = [&](int center, int delta) -> int {
    return clampServoLogicalAngle(HEAD_YAW_INDEX, center + randRange(-delta, delta));
  };
  auto pitchAt = [&](int center, int delta) -> int {
    return clampServoLogicalAngle(HEAD_PITCH_INDEX, center + randRange(-delta, delta));
  };
  auto earAt = [&](int center, int delta) -> int { return clampAngle(center + randRange(-delta, delta)); };
  auto scaleAround = [&](int value, int neutral, float factor) -> int {
    return neutral + static_cast<int>(lroundf((value - neutral) * factor));
  };
  // Idle 动作整体调性：左右转头更克制，耳朵摆动更小。
  auto idleYawAt = [&](int center, int delta) -> int {
    int scaledCenter = scaleAround(center, 90, 0.68f);
    int scaledDelta = max(1, static_cast<int>(lroundf(delta * 0.68f)));
    return yawAt(scaledCenter, scaledDelta);
  };
  auto idlePitchAt = [&](int center, int delta) -> int {
    return pitchAt(center, delta);
  };
  auto idleEarAt = [&](int center, int delta) -> int {
    int scaledCenter = scaleAround(center, 90, 0.60f);
    int scaledDelta = max(1, static_cast<int>(lroundf(delta * 0.60f)));
    return earAt(scaledCenter, scaledDelta);
  };

  uint32_t totalDuration = 0;
  int side = random(2) == 0 ? -1 : 1;
  int altSide = side * -1;

  switch (actionId) {
    case 1:
      totalDuration += addPose(yawAt(90 + side * 10, 4), pitchAt(84, 2), earAt(42, 10), 260, 80);
      totalDuration += addPose(yawAt(90 + side * 18, 3), pitchAt(82, 2), earAt(30, 8), 190, 70);
      totalDuration += addPose(yawAt(90 + side * 12, 5), pitchAt(85, 2), earAt(52, 10), 320, 110);
      break;
    case 2:
      totalDuration += addPose(yawAt(currentYaw() + side * 6, 3), pitchAt(94, 1), earAt(118, 10), 340, 90);
      totalDuration += addPose(yawAt(90 + side * 14, 2), pitchAt(96, 1), earAt(138, 8), 520, 120);
      totalDuration += addPose(yawAt(90 + side * 10, 3), pitchAt(95, 1), earAt(126, 8), 420, 100);
      break;
    case 3:
      totalDuration += addPose(yawAt(90 + side * 12, 4), pitchAt(82, 1), earAt(152, 8), 180, 60);
      totalDuration += addPose(yawAt(90 + side * 24, 3), pitchAt(80, 1), earAt(146, 6), 150, 50);
      totalDuration += addPose(yawAt(90 + side * 24, 2), pitchAt(81, 1), earAt(158, 6), 420, 120);
      break;
    case 4:
      totalDuration += addPose(yawAt(90 + side * 18, 4), pitchAt(95, 2), earAt(106, 8), 280, 90);
      totalDuration += addPose(yawAt(90 + side * 22, 2), pitchAt(97, 1), earAt(118, 8), 380, 120);
      totalDuration += addPose(yawAt(90 + side * 14, 3), pitchAt(96, 2), earAt(110, 8), 260, 90);
      break;
    case 5:
      totalDuration += addPose(yawAt(90 + side * 8, 3), pitchAt(95, 1), earAt(150, 10), 170, 60);
      totalDuration += addPose(yawAt(90 + side * 14, 3), pitchAt(96, 1), earAt(164, 6), 210, 70);
      totalDuration += addPose(yawAt(90 + side * 10, 3), pitchAt(94, 2), earAt(148, 8), 440, 110);
      break;
    case 6:
      totalDuration += addPose(yawAt(90 + side * 16, 4), pitchAt(88, 2), earAt(80, 10), 320, 120);
      totalDuration += addPose(yawAt(90 + side * 22, 3), pitchAt(84, 2), earAt(70, 8), 260, 90);
      totalDuration += addPose(yawAt(90 + side * 18, 4), pitchAt(90, 2), earAt(86, 10), 420, 120);
      break;
    case 7:
      totalDuration += addPose(yawAt(90 + side * 14, 4), pitchAt(84, 1), earAt(32, 8), 220, 60);
      totalDuration += addPose(yawAt(90 + side * 20, 3), pitchAt(83, 1), earAt(28, 6), 360, 110);
      totalDuration += addPose(yawAt(90 + side * 16, 3), pitchAt(85, 2), earAt(40, 8), 260, 90);
      break;
    case 8:
      totalDuration += addPose(yawAt(90 + side * 6, 4), pitchAt(78, 1), earAt(24, 8), 130, 40);
      totalDuration += addPose(yawAt(90 + side * 10, 4), pitchAt(76, 1), earAt(18, 6), 120, 30);
      totalDuration += addPose(yawAt(90 + side * 10, 3), pitchAt(80, 2), earAt(32, 8), 420, 120);
      break;
    case 9:
      totalDuration += addPose(yawAt(90 + side * 10, 4), pitchAt(95, 1), earAt(128, 8), 420, 120);
      totalDuration += addPose(yawAt(90 + side * 16, 3), pitchAt(96, 1), earAt(138, 8), 520, 150);
      totalDuration += addPose(yawAt(90 + side * 12, 4), pitchAt(95, 1), earAt(126, 8), 380, 120);
      break;
    case 10:
      totalDuration += addPose(yawAt(90 + side * 20, 4), pitchAt(87, 1), earAt(88, 6), 260, 70);
      totalDuration += addPose(yawAt(90 + side * 20, 2), pitchAt(86, 1), earAt(92, 4), 500, 140);
      totalDuration += addPose(yawAt(90 + side * 24, 2), pitchAt(86, 1), earAt(84, 4), 260, 70);
      break;
    case 11:
      totalDuration += addPose(idleYawAt(currentYaw(), 3), idlePitchAt(91, 1), idleEarAt(94, 4), 480, 120);
      totalDuration += addPose(idleYawAt(currentYaw(), 3), idlePitchAt(87, 1), idleEarAt(86, 4), 620, 160);
      totalDuration += addPose(idleYawAt(currentYaw() + side * 3, 2), idlePitchAt(90, 1), idleEarAt(92, 4), 520, 140);
      break;
    case 12:
      totalDuration += addPose(idleYawAt(90 - 16, 5), idlePitchAt(89, 2), idleEarAt(84, 6), 360, 110);
      totalDuration += addPose(idleYawAt(90 - 24, 4), idlePitchAt(90, 1), idleEarAt(76, 6), 540, 160);
      totalDuration += addPose(idleYawAt(90 - 18, 4), idlePitchAt(88, 2), idleEarAt(82, 6), 360, 100);
      break;
    case 13:
      totalDuration += addPose(idleYawAt(90 + 16, 5), idlePitchAt(89, 2), idleEarAt(84, 6), 360, 110);
      totalDuration += addPose(idleYawAt(90 + 24, 4), idlePitchAt(90, 1), idleEarAt(76, 6), 540, 160);
      totalDuration += addPose(idleYawAt(90 + 18, 4), idlePitchAt(88, 2), idleEarAt(82, 6), 360, 100);
      break;
    case 14:
      totalDuration += addPose(idleYawAt(currentYaw() + side * 4, 3), idlePitchAt(89, 1), idleEarAt(60, 8), 160, 40);
      totalDuration += addPose(idleYawAt(currentYaw() + side * 4, 3), idlePitchAt(89, 1), idleEarAt(132, 8), 180, 40);
      totalDuration += addPose(idleYawAt(currentYaw() + side * 2, 2), idlePitchAt(90, 1), idleEarAt(74, 6), 140, 30);
      totalDuration += addPose(idleYawAt(currentYaw() + side * 5, 2), idlePitchAt(89, 1), idleEarAt(96, 6), 360, 100);
      break;
    case 15:
      totalDuration += addPose(idleYawAt(90 + side * 6, 3), idlePitchAt(84, 1), idleEarAt(48, 7), 220, 70);
      totalDuration += addPose(idleYawAt(90 + side * 10, 3), idlePitchAt(79, 1), idleEarAt(40, 6), 300, 90);
      totalDuration += addPose(idleYawAt(90 + side * 12, 2), idlePitchAt(76, 1), idleEarAt(34, 5), 440, 120);
      totalDuration += addPose(idleYawAt(90 + side * 8, 3), idlePitchAt(83, 1), idleEarAt(50, 6), 240, 80);
      break;
    case 16:
      totalDuration += addPose(idleYawAt(90 + side * 14, 4), idlePitchAt(92, 2), idleEarAt(116, 10), 420, 130);
      totalDuration += addPose(idleYawAt(90 + altSide * 18, 4), idlePitchAt(89, 2), idleEarAt(82, 10), 520, 150);
      totalDuration += addPose(idleYawAt(90 + side * 8, 4), idlePitchAt(90, 2), idleEarAt(96, 10), 420, 120);
      break;
    case 17:
      totalDuration += addPose(idleYawAt(90 + side * 10, 4), idlePitchAt(94, 2), idleEarAt(108, 8), 340, 100);
      totalDuration += addPose(idleYawAt(90 + altSide * 12, 4), idlePitchAt(82, 2), idleEarAt(60, 8), 360, 100);
      totalDuration += addPose(idleYawAt(90 + side * 6, 3), idlePitchAt(90, 2), idleEarAt(84, 8), 420, 120);
      break;
    case 18:
      totalDuration += addPose(idleYawAt(90 - 22, 4), idlePitchAt(88, 1), idleEarAt(46, 8), 280, 90);
      totalDuration += addPose(idleYawAt(90 + 22, 4), idlePitchAt(88, 1), idleEarAt(46, 8), 420, 120);
      totalDuration += addPose(idleYawAt(90 + side * 10, 4), idlePitchAt(89, 1), idleEarAt(72, 8), 360, 100);
      break;
    case 19:
      totalDuration += addPose(idleYawAt(currentYaw() + side * 3, 2), idlePitchAt(94, 1), idleEarAt(118, 6), 160, 40);
      totalDuration += addPose(idleYawAt(currentYaw() + side * 3, 2), idlePitchAt(84, 1), idleEarAt(76, 6), 120, 30);
      totalDuration += addPose(idleYawAt(currentYaw() + side * 2, 2), idlePitchAt(95, 1), idleEarAt(120, 6), 120, 30);
      totalDuration += addPose(idleYawAt(currentYaw() + side * 3, 2), idlePitchAt(84, 1), idleEarAt(74, 6), 120, 30);
      totalDuration += addPose(idleYawAt(currentYaw() + side * 2, 2), idlePitchAt(95, 1), idleEarAt(122, 6), 120, 30);
      totalDuration += addPose(idleYawAt(currentYaw() + side * 1, 2), idlePitchAt(90, 1), idleEarAt(94, 5), 260, 70);
      break;
    case 20:
      totalDuration += addPose(idleYawAt(90 + side * 12, 3), idlePitchAt(84, 1), idleEarAt(50, 7), 220, 70);
      totalDuration += addPose(idleYawAt(90 + side * 16, 3), idlePitchAt(79, 1), idleEarAt(40, 6), 280, 90);
      totalDuration += addPose(idleYawAt(90 + altSide * 8, 3), idlePitchAt(87, 1), idleEarAt(98, 7), 260, 80);
      totalDuration += addPose(idleYawAt(90 + side * 8, 3), idlePitchAt(85, 1), idleEarAt(62, 7), 260, 80);
      break;
    default:
      totalDuration += addPose(yawAt(90, 4), pitchAt(90, 2), earAt(90, 6), 260, 80);
      break;
  }
  return totalDuration;
}

int emotionToBuiltinActionId(const String& emotion) {
  String key = emotion;
  key.toLowerCase();
  if (key == "happy" || key == "excited" || key == "love") return 1;
  if (key == "sad" || key == "cry") return 2;
  if (key == "angry") return 3;
  if (key == "shy") return 4;
  if (key == "fear") return 5;
  if (key == "thinking" || key == "confused") return 6;
  if (key == "listening") return 7;
  if (key == "surprised" || key == "surprise") return 8;
  if (key == "sleepy" || key == "boring") return 9;
  if (key == "speechless") return 10;
  return 0;
}

String ipToString(const IPAddress& ip) {
  return String(ip[0]) + "." + String(ip[1]) + "." + String(ip[2]) + "." + String(ip[3]);
}

void drawCenteredText(const String& text, int y, uint16_t color, uint8_t size) {
  int16_t x1, y1;
  uint16_t w, h;
  tft.setTextSize(size);
  tft.getTextBounds(text.c_str(), 0, 0, &x1, &y1, &w, &h);
  int x = (TFT_W - static_cast<int>(w)) / 2;
  tft.setCursor(max(0, x), y);
  tft.setTextColor(color);
  tft.print(text);
}

void setTftRotationIfNeeded(uint8_t rotation) {
  rotation = constrain(rotation, 0, 3);
  if (currentTftRotation == rotation) return;
  tft.applyRotation(rotation);
  currentTftRotation = rotation;
}

void initAnimScaleMaps() {
  const float centerX = TFT_W * 0.5f;
  const float centerY = TFT_H * 0.5f;
  for (int x = 0; x < TFT_W; ++x) {
    int mapped = static_cast<int>(lroundf(centerX + (static_cast<float>(x) - centerX) / LOCAL_ANIM_SCALE_X));
    animScaleXMap[x] = static_cast<int16_t>(constrain(mapped, 0, TFT_W - 1));
  }
  for (int y = 0; y < TFT_H; ++y) {
    int mapped = static_cast<int>(lroundf(centerY + (static_cast<float>(y) - centerY) / LOCAL_ANIM_SCALE_Y));
    animScaleYMap[y] = static_cast<int16_t>(constrain(mapped, 0, TFT_H - 1));
  }
}

void scaleAnimationBuffer(const uint16_t* src, uint16_t* dst) {
  if (src == nullptr || dst == nullptr) return;
  for (int y = 0; y < TFT_H; ++y) {
    const uint16_t* srcRow = src + static_cast<int>(animScaleYMap[y]) * TFT_W;
    uint16_t* dstRow = dst + y * TFT_W;
    for (int x = 0; x < TFT_W; ++x) {
      dstRow[x] = srcRow[animScaleXMap[x]];
    }
  }
}

inline uint8_t rgb565Luma(uint16_t px) {
  uint8_t r = static_cast<uint8_t>(((px >> 11) & 0x1F) * 255 / 31);
  uint8_t g = static_cast<uint8_t>(((px >> 5) & 0x3F) * 255 / 63);
  uint8_t b = static_cast<uint8_t>((px & 0x1F) * 255 / 31);
  return static_cast<uint8_t>((r * 30 + g * 59 + b * 11) / 100);
}

bool loadMouthMask() {
  if (mouthMaskBuffer == nullptr || mouthMaskAlpha == nullptr) return false;
  if (!displayJPEG(MOUTH_MASK_PATH, mouthMaskBuffer, false, LOCAL_ANIM_ROTATE_CCW_90)) {
    LOGF("[MOUTH] mask not found: %s\n", MOUTH_MASK_PATH);
    return false;
  }
  const int radius = MOUTH_MASK_FEATHER_RADIUS;
  const int kernelSize = radius * 2 + 1;
  const int kernelArea = kernelSize * kernelSize;
  for (int y = 0; y < TFT_H; ++y) {
    for (int x = 0; x < TFT_W; ++x) {
      int blackCount = 0;
      for (int ky = -radius; ky <= radius; ++ky) {
        int sy = constrain(y + ky, 0, TFT_H - 1);
        const uint16_t* row = mouthMaskBuffer + sy * TFT_W;
        for (int kx = -radius; kx <= radius; ++kx) {
          int sx = constrain(x + kx, 0, TFT_W - 1);
          if (rgb565Luma(row[sx]) < MOUTH_MASK_THRESHOLD) {
            blackCount++;
          }
        }
      }
      mouthMaskAlpha[y * TFT_W + x] = static_cast<uint8_t>((blackCount * 255) / kernelArea);
    }
  }
  LOGF("[MOUTH] mask loaded with feather radius=%d\n", radius);
  return true;
}

void scanMouthAssets() {
  const char* sources[MAX_MOUTH_ANIMS] = {MOUTH_SMALL_PATH, MOUTH_BIG_PATH, MOUTH_WIDE_PATH, MOUTH_ROUND_PATH};
  mouthAssetsReady = false;
  for (int i = 0; i < MAX_MOUTH_ANIMS; ++i) {
    mouthFrames[i] = 0;
    mouthPaths[i][0] = '\0';
    if (!LittleFS.exists(sources[i])) continue;
    int frames = countAnimFrames(sources[i]);
    if (frames <= 0) continue;
    strncpy(mouthPaths[i], sources[i], sizeof(mouthPaths[i]) - 1);
    mouthPaths[i][sizeof(mouthPaths[i]) - 1] = '\0';
    mouthFrames[i] = frames;
    mouthAssetsReady = true;
    LOGF("[MOUTH] found %s (%d frames)\n", mouthPaths[i], frames);
  }
  if (mouthAssetsReady) {
    mouthAssetsReady = loadMouthMask();
  }
}

bool isTalkingAnim(int animId) {
  return animId == 2 || animId == 3;
}

void notifyExpressionAnimDone() {
  if (!expressionAnimNotifyPending) return;
  expressionAnimNotifyPending = false;
  if (audWsReady && wsAud.available() && expressionAnimNotifyToken.length() > 0) {
    wsAud.send("ANIM_DONE:" + expressionAnimNotifyToken);
  }
  expressionAnimNotifyToken = "";
}

int pickTalkingAnim() {
  bool has2 = isAnimAvailable(2);
  bool has3 = isAnimAvailable(3);
  if (has2 && has3) return (random(2) == 0) ? 2 : 3;
  if (has2) return 2;
  if (has3) return 3;
  return 0;
}

void resetMouthState() {
  mouthVisible = false;
  mouthCurrentType = 0;
  mouthEnergySmoothed = 0.0f;
  mouthBrightnessSmoothed = 0.0f;
  mouthFrameCursor = 0.0f;
  mouthPlaybackStep = 0.8f;
}

void startTalkingAnimationLoop() {
  if (!mouthAssetsReady || !LOCAL_EXPRESSION_ANIMATION_ENABLED || screenPageMode != SCREEN_PAGE_EXPRESSION) return;
  int talkAnim = pickTalkingAnim();
  if (talkAnim <= 0) return;
  mouthTalkingMode = true;
  resetMouthState();
  animLoop = true;
  if (frameBuffer != nullptr && crossfadeOldBuf != nullptr && crossfadeNewBuf != nullptr && currentAnim > 0 &&
      currentAnim != talkAnim) {
    memcpy(crossfadeOldBuf, frameBuffer, TFT_W * TFT_H * sizeof(uint16_t));
    char filename[32];
    sprintf(filename, "%s/%04d.jpg", animPaths[talkAnim], 1);
    if (displayAnimJPEG(filename, crossfadeNewBuf, false, talkAnim)) {
      crossfadeActive = true;
      crossfadeStartMs = millis();
      currentAnim = talkAnim;
      currentFrame = 2;
      lastFrameTime = 0;
      return;
    }
  }
  currentAnim = talkAnim;
  currentFrame = 1;
  lastFrameTime = 0;
}

void stopTalkingAnimationLoop() {
  mouthTalkingMode = false;
  resetMouthState();
  if (screenPageMode == SCREEN_PAGE_EXPRESSION && maxAnimIndex > 0) {
    startIdleAnimationLoop();
  }
}

void updateMouthFromAudioChunk(const int16_t* samples, size_t sampleCount) {
  if (!mouthTalkingMode || !ttsPlaying || samples == nullptr || sampleCount == 0) {
    mouthVisible = false;
    return;
  }
  double sumAbs = 0.0;
  double sumDiff = 0.0;
  int16_t prev = samples[0];
  for (size_t i = 0; i < sampleCount; ++i) {
    int16_t s = samples[i];
    sumAbs += abs(static_cast<int>(s));
    if (i > 0) sumDiff += abs(static_cast<int>(s) - static_cast<int>(prev));
    prev = s;
  }
  float energy = static_cast<float>(sumAbs / sampleCount / 32768.0);
  double denom = (sumAbs > 1.0) ? sumAbs : 1.0;
  float brightness = static_cast<float>(sumDiff / denom);
  mouthEnergySmoothed = mouthEnergySmoothed * 0.72f + energy * 0.28f;
  mouthBrightnessSmoothed = mouthBrightnessSmoothed * 0.72f + brightness * 0.28f;

  if (mouthEnergySmoothed < 0.02f) {
    mouthVisible = false;
    return;
  }

  mouthVisible = true;
  if (mouthBrightnessSmoothed > 0.62f) {
    mouthCurrentType = 2;  // wide
  } else if (mouthBrightnessSmoothed < 0.36f) {
    mouthCurrentType = 3;  // round
  } else if (mouthEnergySmoothed > 0.11f) {
    mouthCurrentType = 1;  // big
  } else {
    mouthCurrentType = 0;  // small
  }

  mouthPlaybackStep = 0.45f + constrain(mouthEnergySmoothed * 5.5f, 0.0f, 1.8f);
}

void applyMouthOverlay(uint16_t* targetBuffer) {
  if (targetBuffer == nullptr || !mouthTalkingMode || !ttsPlaying || !mouthVisible || !mouthAssetsReady ||
      mouthMaskAlpha == nullptr || mouthFrameBuffer == nullptr) {
    return;
  }
  if (mouthCurrentType >= MAX_MOUTH_ANIMS || mouthFrames[mouthCurrentType] <= 0) return;

  mouthFrameCursor += mouthPlaybackStep;
  while (mouthFrameCursor >= mouthFrames[mouthCurrentType]) {
    mouthFrameCursor -= mouthFrames[mouthCurrentType];
  }
  int frameIdx = 1 + static_cast<int>(mouthFrameCursor);
  char filename[40];
  sprintf(filename, "%s/%04d.jpg", mouthPaths[mouthCurrentType], frameIdx);
  if (!displayJPEG(filename, mouthFrameBuffer, false, LOCAL_ANIM_ROTATE_CCW_90)) return;

  for (int i = 0; i < TFT_W * TFT_H; ++i) {
    uint8_t alpha = mouthMaskAlpha[i];
    if (!alpha) continue;
    if (rgb565Luma(mouthFrameBuffer[i]) > 240) continue;
    targetBuffer[i] = (alpha >= 250) ? mouthFrameBuffer[i] : blendRGB565(targetBuffer[i], mouthFrameBuffer[i], alpha);
  }
}

bool displayAnimJPEG(const char* filename, uint16_t* targetBuffer, bool drawIfBuffered, int animId) {
  if (!displayJPEG(filename, targetBuffer, false, LOCAL_ANIM_ROTATE_CCW_90)) return false;
  if (isTalkingAnim(animId)) {
    applyMouthOverlay(targetBuffer);
  }
  if (targetBuffer != nullptr && drawIfBuffered) {
    tft.drawRGBBitmap(0, 0, targetBuffer, TFT_W, TFT_H);
  }
  return true;
}

void drawStatusBanner(const String& line1, const String& line2) {
  // 采用和表情帧完全一样的"占满屏"套路：
  //   1. 在 PSRAM 里拿一块 pageW x pageH 的横向画布，先按人眼正方向画 banner；
  //   2. 把画布做 CCW 90° 旋转到 240 x 284 的输出 buffer；
  //   3. setTftRotationIfNeeded(0) 下 drawRGBBitmap 推全屏。
  // 这样完全绕过 rotation=2/3 的 offset 问题，一定能像表情动画一样占满整块屏。
  const int pageW = 284;
  const int pageH = 240;

  if (frameBuffer == nullptr || crossfadeOldBuf == nullptr) {
    // PSRAM 不可用时的兜底：直接竖屏铺一个背景，至少不花屏。
    setTftRotationIfNeeded(0);
    tft.fillScreen(tft.color565(10, 14, 22));
    return;
  }

  ExternalBufferCanvas canvas(pageW, pageH, frameBuffer);

  const uint16_t COLOR_BG      = tft.color565(10, 14, 22);
  const uint16_t COLOR_PRIMARY = tft.color565(90, 210, 240);
  const uint16_t COLOR_ACCENT  = tft.color565(170, 240, 255);
  const uint16_t COLOR_MUTED   = tft.color565(70, 120, 160);

  canvas.fillScreen(COLOR_BG);
  canvas.fillRect(0, 0, pageW, 3, COLOR_PRIMARY);
  canvas.fillRect(0, pageH - 3, pageW, 3, COLOR_PRIMARY);
  canvas.fillRect(14, 14, 6, 22, COLOR_PRIMARY);
  canvas.fillRect(pageW - 20, pageH - 36, 6, 22, COLOR_PRIMARY);

  auto printCentered = [&](const String& s, int y, uint16_t color, uint8_t size) {
    int16_t bx, by;
    uint16_t bw, bh;
    canvas.setTextSize(size);
    canvas.getTextBounds(s.c_str(), 0, 0, &bx, &by, &bw, &bh);
    int x = (pageW - static_cast<int>(bw)) / 2;
    canvas.setCursor(max(0, x), y);
    canvas.setTextColor(color);
    canvas.print(s);
  };

  printCentered("CAT ROBOT", 54, COLOR_ACCENT, 3);
  canvas.drawFastHLine((pageW - 120) / 2, 98, 120, COLOR_MUTED);
  printCentered(line1, 114, COLOR_PRIMARY, 2);
  printCentered(line2, 148, COLOR_MUTED, 1);

  int barW = pageW - 60;
  int barX = (pageW - barW) / 2;
  canvas.drawRoundRect(barX, pageH - 46, barW, 26, 8, COLOR_PRIMARY);
  printCentered(ipToString(WiFi.localIP()), pageH - 38, COLOR_ACCENT, 1);

  // CCW 90° 把 pageW x pageH 的画布旋转到 TFT_W x TFT_H（240 x 284）输出 buffer：
  //   dst[pageW-1 - x][y] = src[y][x]
  for (int y = 0; y < pageH; ++y) {
    const uint16_t* srcRow = frameBuffer + static_cast<int32_t>(y) * pageW;
    int ny_base = pageW - 1;
    for (int x = 0; x < pageW; ++x) {
      int nx = y;
      int ny = ny_base - x;
      crossfadeOldBuf[static_cast<int32_t>(ny) * TFT_W + nx] = srcRow[x];
    }
  }

  setTftRotationIfNeeded(0);
  tft.drawRGBBitmap(0, 0, crossfadeOldBuf, TFT_W, TFT_H);
}

void drawRemoteScreenWaiting(const char* title, const char* subtitle) {
  setTftRotationIfNeeded(0);
  tft.fillScreen(ST77XX_WHITE);
  tft.fillRoundRect(18, 26, 204, 86, 18, tft.color565(36, 44, 67));
  tft.fillCircle(58, 68, 22, tft.color565(255, 166, 110));
  tft.setTextColor(ST77XX_WHITE);
  tft.setTextSize(2);
  tft.setCursor(96, 48);
  tft.print(title);
  tft.setTextColor(tft.color565(255, 220, 180));
  tft.setTextSize(1);
  tft.setCursor(96, 74);
  tft.print(subtitle);
  tft.setTextColor(tft.color565(80, 88, 100));
  tft.setCursor(22, 152);
  tft.print("Waiting for server screen...");
  tft.setCursor(22, 172);
  tft.print("WebUI will push JPEG frames.");
}

void drawCatExpressionScreen(bool blink) {
  if (screenPageMode != SCREEN_PAGE_EXPRESSION) return;
  (void)blink;
  setTftRotationIfNeeded(0);
  tft.fillScreen(tft.color565(10, 14, 22));
  tft.fillRoundRect(12, 14, 216, 40, 12, tft.color565(28, 38, 56));
  drawCenteredText("DEVICE INFO", 26, ST77XX_CYAN, 2);

  tft.drawRoundRect(12, 64, 216, 208, 12, tft.color565(70, 96, 132));
  tft.setTextSize(1);
  tft.setTextColor(ST77XX_WHITE);

  int y = 78;
  auto printLine = [&](const String& label, const String& value, uint16_t color) {
    tft.setTextColor(tft.color565(160, 184, 220));
    tft.setCursor(22, y);
    tft.print(label);
    tft.setTextColor(color);
    tft.setCursor(98, y);
    tft.print(value);
    y += 22;
  };

  printLine("wifi", WiFi.status() == WL_CONNECTED ? "online" : "offline",
            WiFi.status() == WL_CONNECTED ? ST77XX_GREEN : ST77XX_RED);
  printLine("ip", ipToString(WiFi.localIP()), ST77XX_YELLOW);
  printLine("ws cam", camWsReady ? "connected" : "offline", camWsReady ? ST77XX_GREEN : ST77XX_RED);
  printLine("ws aud", audWsReady ? "connected" : "offline", audWsReady ? ST77XX_GREEN : ST77XX_RED);
  printLine("audio", ttsPlaying ? "tts_playing" : (runAudioStream ? "mic_stream" : "idle"),
            ttsPlaying ? ST77XX_YELLOW : ST77XX_WHITE);
  printLine("emotion", currentEmotion, tft.color565(255, 140, 96));
  printLine("camera fps", String(effectiveCameraFps()), ST77XX_WHITE);
  int animFoundCount = 0;
  int animTotalFrames = 0;
  for (int i = 1; i <= maxAnimIndex; ++i) {
    if (animFrames[i] > 0) {
      animFoundCount++;
      animTotalFrames += animFrames[i];
    }
  }
  printLine("anim folders", animFoundCount > 0 ? String(animFoundCount) + " found" : String("not found"),
            animFoundCount > 0 ? ST77XX_GREEN : ST77XX_RED);
  printLine("anim frames", String(animTotalFrames), ST77XX_WHITE);

  tft.setTextColor(tft.color565(120, 144, 178));
  tft.setCursor(20, 252);
  tft.print(animFoundCount > 0 ? "Animations ready. Playing soon..." : "No uploaded animation. Showing info page.");
}

void drawCoverInfoScreen() {
  if (screenPageMode != SCREEN_PAGE_EXPRESSION) return;
  setTftRotationIfNeeded(3);
  const int pageW = 284;
  const int pageH = 240;
  tft.fillScreen(tft.color565(10, 14, 22));
  tft.fillRoundRect(12, 12, pageW - 24, 34, 10, tft.color565(28, 38, 56));
  tft.setTextColor(ST77XX_CYAN);
  tft.setTextSize(2);
  tft.setCursor(18, 20);
  tft.print("DEVICE INFO");

  tft.drawRoundRect(12, 56, pageW - 24, pageH - 72, 12, tft.color565(70, 96, 132));
  tft.setTextSize(1);
  int y = 70;
  auto printLine = [&](const String& label, const String& value, uint16_t color) {
    tft.setTextColor(tft.color565(160, 184, 220));
    tft.setCursor(20, y);
    tft.print(label);
    tft.setTextColor(color);
    tft.setCursor(102, y);
    tft.print(value);
    y += 18;
  };

  int animFoundCount = 0;
  int animTotalFrames = 0;
  for (int i = 1; i <= maxAnimIndex; ++i) {
    if (animFrames[i] > 0) {
      animFoundCount++;
      animTotalFrames += animFrames[i];
    }
  }

  printLine("wifi", WiFi.status() == WL_CONNECTED ? "online" : "offline",
            WiFi.status() == WL_CONNECTED ? ST77XX_GREEN : ST77XX_RED);
  printLine("ip", ipToString(WiFi.localIP()), ST77XX_YELLOW);
  printLine("ws cam", camWsReady ? "connected" : "offline", camWsReady ? ST77XX_GREEN : ST77XX_RED);
  printLine("ws aud", audWsReady ? "connected" : "offline", audWsReady ? ST77XX_GREEN : ST77XX_RED);
  printLine("audio", String(speakerVolumePercent) + "%", ST77XX_WHITE);
  printLine("emotion", currentEmotion, tft.color565(255, 140, 96));
  printLine("anim", String(animFoundCount) + "/" + String(animTotalFrames), ST77XX_WHITE);

  tft.setTextColor(tft.color565(120, 144, 178));
  tft.setCursor(18, pageH - 18);
  tft.print("Cover lens again to return to animation");
}

int jpegDrawCallback(JPEGDRAW* draw) {
  if (visionPreviewDecodeActive && visionPreviewBuffer != nullptr && visionPreviewCropW > 0 &&
      visionPreviewCropH > 0) {
    uint16_t* src = reinterpret_cast<uint16_t*>(draw->pPixels);
    for (int y = 0; y < draw->iHeight; ++y) {
      for (int x = 0; x < draw->iWidth; ++x) {
        int srcX = draw->x + x;
        int srcY = draw->y + y;
        if (srcX < visionPreviewCropX || srcX >= visionPreviewCropX + visionPreviewCropW) continue;
        if (srcY < visionPreviewCropY || srcY >= visionPreviewCropY + visionPreviewCropH) continue;

        // 先把摄像头画面 cover 到横屏预览框 132x92，再跟整页一样 CCW 90° 到 92x132。
        int u = ((srcX - visionPreviewCropX) * VISION_LOCAL_PREVIEW_H) / visionPreviewCropW;
        int v = ((srcY - visionPreviewCropY) * VISION_LOCAL_PREVIEW_W) / visionPreviewCropH;
        int destX = v;
        int destY = VISION_LOCAL_PREVIEW_H - 1 - u;
        if (destX < 0 || destX >= VISION_LOCAL_PREVIEW_W) continue;
        if (destY < 0 || destY >= VISION_LOCAL_PREVIEW_H) continue;
        if (visionPreviewDecodeTarget != nullptr) {
          visionPreviewDecodeTarget[destY * VISION_LOCAL_PREVIEW_W + destX] = src[y * draw->iWidth + x];
        }
      }
    }
    return 1;
  }

  if (jpegDecodeTarget == nullptr && !jpegDecodeRotateCCW90) {
    tft.drawRGBBitmap(draw->x, draw->y, reinterpret_cast<uint16_t*>(draw->pPixels), draw->iWidth, draw->iHeight);
    return 1;
  }

  if (jpegDecodeTarget == nullptr) return 1;

  uint16_t* src = reinterpret_cast<uint16_t*>(draw->pPixels);
  for (int y = 0; y < draw->iHeight; ++y) {
    for (int x = 0; x < draw->iWidth; ++x) {
      int srcX = draw->x + x;
      int srcY = draw->y + y;
      int destX = srcX;
      int destY = srcY;
      if (jpegDecodeRotateCCW90) {
        destX = srcY;
        destY = TFT_H - 1 - srcX;
      }
      if (!jpegDecodeRotateCCW90) {
        if (destX < 0 || destX >= TFT_W) continue;
        if (destY < 0 || destY >= TFT_H) continue;
        jpegDecodeTarget[destY * TFT_W + destX] = src[y * draw->iWidth + x];
        continue;
      }
      if (destX < 0 || destX >= TFT_W) continue;
      if (destY < 0 || destY >= TFT_H) continue;
      jpegDecodeTarget[destY * TFT_W + destX] = src[y * draw->iWidth + x];
    }
  }
  return 1;
}

static inline uint16_t blendRGB565(uint16_t oldPx, uint16_t newPx, uint16_t alpha) {
  uint16_t invAlpha = 256 - alpha;
  uint16_t r = ((((oldPx >> 11) & 0x1F) * invAlpha + ((newPx >> 11) & 0x1F) * alpha) >> 8);
  uint16_t g = ((((oldPx >> 5) & 0x3F) * invAlpha + ((newPx >> 5) & 0x3F) * alpha) >> 8);
  uint16_t b = ((((oldPx)&0x1F) * invAlpha + ((newPx)&0x1F) * alpha) >> 8);
  return (r << 11) | (g << 5) | b;
}

bool decodeJpegBytes(const uint8_t* data, size_t len, uint16_t* targetBuffer, bool drawIfBuffered,
                     bool rotateCCW90 = false) {
  if (data == nullptr || len == 0 || len > JPEG_BUFFER_SIZE) return false;
  if (!lockJpegDecoder()) return false;
  jpegDecodeTarget = targetBuffer;
  jpegDecodeRotateCCW90 = rotateCCW90;
  bool ok = jpeg.openRAM(const_cast<uint8_t*>(data), len, jpegDrawCallback);
  if (!ok) {
    jpegDecodeTarget = nullptr;
    jpegDecodeRotateCCW90 = false;
    unlockJpegDecoder();
    return false;
  }
  jpeg.setPixelType(RGB565_LITTLE_ENDIAN);
  jpeg.decode(0, 0, 0);
  jpeg.close();
  jpegDecodeTarget = nullptr;
  jpegDecodeRotateCCW90 = false;
  unlockJpegDecoder();
  if (targetBuffer != nullptr && drawIfBuffered) {
    tft.drawRGBBitmap(0, 0, targetBuffer, TFT_W, TFT_H);
  }
  return true;
}

bool displayJPEG(const char* filename, uint16_t* targetBuffer, bool drawIfBuffered, bool rotateCCW90 = false) {
  File file = LittleFS.open(filename, "r");
  if (!file) return false;
  size_t fileSize = file.size();
  if (fileSize == 0 || fileSize > JPEG_BUFFER_SIZE) {
    file.close();
    return false;
  }
  size_t bytesRead = file.read(jpegBuffer, fileSize);
  file.close();
  if (bytesRead != fileSize) return false;
  if (rotateCCW90 && targetBuffer != nullptr && animDecodeBuffer != nullptr) {
    if (!decodeJpegBytes(jpegBuffer, fileSize, animDecodeBuffer, false, true)) return false;
    scaleAnimationBuffer(animDecodeBuffer, targetBuffer);
    if (drawIfBuffered) {
      tft.drawRGBBitmap(0, 0, targetBuffer, TFT_W, TFT_H);
    }
    return true;
  }
  return decodeJpegBytes(jpegBuffer, fileSize, targetBuffer, drawIfBuffered, rotateCCW90);
}

void compositeVisionPreviewIntoFrameBuffer() {
  if (frameBuffer == nullptr || visionPreviewBuffer == nullptr || !visionPreviewValid) return;
  for (int y = 0; y < VISION_LOCAL_PREVIEW_H; ++y) {
    int dstY = VISION_LOCAL_PREVIEW_Y + y;
    if (dstY < 0 || dstY >= TFT_H) continue;
    uint16_t* dst = frameBuffer + static_cast<int32_t>(dstY) * TFT_W + VISION_LOCAL_PREVIEW_X;
    const uint16_t* src = visionPreviewBuffer + static_cast<int32_t>(y) * VISION_LOCAL_PREVIEW_W;
    memcpy(dst, src, VISION_LOCAL_PREVIEW_W * sizeof(uint16_t));
  }
}

void updateRemoteScreenFrame(const uint8_t* data, size_t len) {
  if (screenPageMode == SCREEN_PAGE_EXPRESSION) return;
  if (screenPageMode == SCREEN_PAGE_VISION_DIALOG && frameBuffer != nullptr) {
    if (decodeJpegBytes(data, len, frameBuffer, false, false)) {
      compositeVisionPreviewIntoFrameBuffer();
      tft.drawRGBBitmap(0, 0, frameBuffer, TFT_W, TFT_H);
    }
    return;
  }
  decodeJpegBytes(data, len, frameBuffer, frameBuffer != nullptr, false);
}

void drawLocalVisionCameraPreview(camera_fb_t* fb) {
  if (screenPageMode != SCREEN_PAGE_VISION_DIALOG || fb == nullptr || fb->format != PIXFORMAT_JPEG ||
      visionPreviewBuffer == nullptr || visionPreviewDecodeBuffer == nullptr) {
    return;
  }

  int srcW = fb->width;
  int srcH = fb->height;
  if (srcW <= 0 || srcH <= 0) return;

  const float targetAspect = static_cast<float>(VISION_LOCAL_PREVIEW_H) /
                             static_cast<float>(VISION_LOCAL_PREVIEW_W);
  const float srcAspect = static_cast<float>(srcW) / static_cast<float>(srcH);
  if (srcAspect > targetAspect) {
    visionPreviewCropH = srcH;
    visionPreviewCropW = max(1, static_cast<int>(srcH * targetAspect));
    visionPreviewCropX = (srcW - visionPreviewCropW) / 2;
    visionPreviewCropY = 0;
  } else {
    visionPreviewCropW = srcW;
    visionPreviewCropH = max(1, static_cast<int>(srcW / targetAspect));
    visionPreviewCropX = 0;
    visionPreviewCropY = (srcH - visionPreviewCropH) / 2;
  }

  int previewPixels = VISION_LOCAL_PREVIEW_W * VISION_LOCAL_PREVIEW_H;
  if (visionPreviewValid) {
    memcpy(visionPreviewDecodeBuffer, visionPreviewBuffer, previewPixels * sizeof(uint16_t));
  } else {
    for (int i = 0; i < previewPixels; ++i) {
      visionPreviewDecodeBuffer[i] = tft.color565(20, 26, 38);
    }
  }

  if (!lockJpegDecoder(pdMS_TO_TICKS(60))) return;
  visionPreviewDecodeTarget = visionPreviewDecodeBuffer;
  visionPreviewDecodeActive = true;
  bool ok = jpeg.openRAM(reinterpret_cast<uint8_t*>(fb->buf), fb->len, jpegDrawCallback);
  if (ok) {
    jpeg.setPixelType(RGB565_LITTLE_ENDIAN);
    jpeg.decode(0, 0, 0);
    jpeg.close();
    memcpy(visionPreviewBuffer, visionPreviewDecodeBuffer, previewPixels * sizeof(uint16_t));
    tft.drawRGBBitmap(
        VISION_LOCAL_PREVIEW_X, VISION_LOCAL_PREVIEW_Y,
        visionPreviewBuffer, VISION_LOCAL_PREVIEW_W, VISION_LOCAL_PREVIEW_H
    );
    visionPreviewValid = true;
  }
  visionPreviewDecodeActive = false;
  visionPreviewDecodeTarget = nullptr;
  visionPreviewCropX = 0;
  visionPreviewCropY = 0;
  visionPreviewCropW = 0;
  visionPreviewCropH = 0;
  unlockJpegDecoder();
}

int effectiveCameraFps() {
  if (ttsPlaying || ttsAudioAvailable) {
    return 0;
  }
  int fps = gTargetFps;
  return (fps > 0) ? fps : 0;
}

bool audioPriorityModeActive() {
  return ttsPlaying || ttsAudioAvailable;
}

int countAnimFrames(const char* animPath) {
  int count = 0;
  char filename[32];
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

bool isAnimAvailable(int animId) {
  return animId >= 1 && animId <= maxAnimIndex && animFrames[animId] > 0;
}

int firstAvailableAnim() {
  for (int i = 1; i <= maxAnimIndex; ++i) {
    if (animFrames[i] > 0) return i;
  }
  return 0;
}

int pickWeightedAnim(const int* animIds, const int* weights, int count) {
  int totalWeight = 0;
  for (int i = 0; i < count; ++i) {
    if (weights[i] > 0 && isAnimAvailable(animIds[i])) {
      totalWeight += weights[i];
    }
  }
  if (totalWeight <= 0) return 0;

  int roll = random(totalWeight);
  int cumulative = 0;
  for (int i = 0; i < count; ++i) {
    if (weights[i] <= 0 || !isAnimAvailable(animIds[i])) continue;
    cumulative += weights[i];
    if (roll < cumulative) return animIds[i];
  }
  return 0;
}

int pickRandomAnim() {
  // anim1-8: idle1/idle2/idle3/surprise/happy/cry/speechless/boring
  // 权重: 15/30/15/5/15/5/5/10
  const int animIds[] = {1, 2, 3, 4, 5, 6, 7, 8};
  const int weights[] = {15, 30, 15, 5, 15, 5, 5, 10};
  int nextAnim = pickWeightedAnim(animIds, weights, 8);
  return nextAnim > 0 ? nextAnim : firstAvailableAnim();
}

int pickIdleLikeAnim() {
  const int animIds[] = {1, 2, 3, 8};
  const int weights[] = {15, 30, 15, 10};
  int nextAnim = pickWeightedAnim(animIds, weights, 4);
  return nextAnim > 0 ? nextAnim : pickRandomAnim();
}

void startIdleAnimationLoop() {
  if (maxAnimIndex <= 0) return;
  mouthTalkingMode = false;
  resetMouthState();
  expressionAnimNotifyPending = false;
  expressionAnimNotifyToken = "";
  animLoop = true;
  currentAnim = pickRandomAnim();
  currentFrame = 1;
  lastFrameTime = 0;
  crossfadeActive = false;
  expressionDirty = false;
}

int expressionToAnim(const String& expr) {
  String key = expr;
  key.toLowerCase();
  if (key == "surprised" || key == "surprise") return isAnimAvailable(4) ? 4 : pickRandomAnim();
  if (key == "happy" || key == "excited" || key == "love") return isAnimAvailable(5) ? 5 : pickRandomAnim();
  if (key == "sad" || key == "fear" || key == "cry") return isAnimAvailable(6) ? 6 : pickRandomAnim();
  if (key == "speechless" || key == "shy" || key == "confused" || key == "angry") {
    return isAnimAvailable(7) ? 7 : pickRandomAnim();
  }
  if (key == "boring" || key == "sleepy" || key == "tired") return isAnimAvailable(8) ? 8 : pickIdleLikeAnim();
  if (key == "neutral" || key == "thinking" || key == "sleepy" || key == "confused" || key == "idle" ||
      key == "listening") {
    return pickIdleLikeAnim();
  }
  return pickRandomAnim();
}

void startExpressionAnimation(int animId) {
  if (animId < 1 || animId > maxAnimIndex || animFrames[animId] == 0) {
    return;
  }
  if (!mouthTalkingMode) resetMouthState();
  animLoop = false;
  if (frameBuffer != nullptr && crossfadeOldBuf != nullptr && crossfadeNewBuf != nullptr && currentAnim > 0) {
    memcpy(crossfadeOldBuf, frameBuffer, TFT_W * TFT_H * sizeof(uint16_t));
    char filename[32];
    sprintf(filename, "%s/%04d.jpg", animPaths[animId], 1);
    if (displayAnimJPEG(filename, crossfadeNewBuf, false, animId)) {
      crossfadeActive = true;
      crossfadeStartMs = millis();
      currentAnim = animId;
      currentFrame = 2;
      lastFrameTime = 0;
      return;
    }
  }
  currentAnim = animId;
  currentFrame = 1;
  lastFrameTime = 0;
}

bool updateCrossfade() {
  if (!crossfadeActive || frameBuffer == nullptr || crossfadeOldBuf == nullptr || crossfadeNewBuf == nullptr) {
    return false;
  }
  uint32_t elapsed = millis() - crossfadeStartMs;
  if (elapsed >= CROSSFADE_DURATION_MS) {
    crossfadeActive = false;
    memcpy(frameBuffer, crossfadeNewBuf, TFT_W * TFT_H * sizeof(uint16_t));
    tft.drawRGBBitmap(0, 0, frameBuffer, TFT_W, TFT_H);
    return false;
  }
  uint16_t alpha = static_cast<uint16_t>((elapsed * 256) / CROSSFADE_DURATION_MS);
  int total = TFT_W * TFT_H;
  for (int i = 0; i < total; ++i) {
    frameBuffer[i] = blendRGB565(crossfadeOldBuf[i], crossfadeNewBuf[i], alpha);
  }
  tft.drawRGBBitmap(0, 0, frameBuffer, TFT_W, TFT_H);
  return true;
}

void playAnimationFrame() {
  if (currentAnim < 1 || currentAnim > maxAnimIndex || animFrames[currentAnim] == 0) return;
  char filename[32];
  sprintf(filename, "%s/%04d.jpg", animPaths[currentAnim], currentFrame);
  if (displayAnimJPEG(filename, frameBuffer, frameBuffer != nullptr, currentAnim)) {
    currentFrame++;
    if (currentFrame > animFrames[currentAnim]) {
      if (animLoop) {
        int nextAnim = mouthTalkingMode ? pickTalkingAnim() : pickRandomAnim();
        if (nextAnim > 0 && nextAnim != currentAnim) {
          if (frameBuffer != nullptr && crossfadeOldBuf != nullptr && crossfadeNewBuf != nullptr) {
            memcpy(crossfadeOldBuf, frameBuffer, TFT_W * TFT_H * sizeof(uint16_t));
            char nextFilename[32];
            sprintf(nextFilename, "%s/%04d.jpg", animPaths[nextAnim], 1);
            if (displayAnimJPEG(nextFilename, crossfadeNewBuf, false, nextAnim)) {
              crossfadeActive = true;
              crossfadeStartMs = millis();
              currentAnim = nextAnim;
              currentFrame = 2;
            } else {
              currentAnim = nextAnim;
              currentFrame = 1;
            }
          } else {
            currentAnim = nextAnim;
            currentFrame = 1;
          }
        } else {
          currentFrame = 1;
        }
      } else {
        notifyExpressionAnimDone();
        int nextAnim = mouthTalkingMode ? pickTalkingAnim() : pickRandomAnim();
        if (nextAnim > 0) {
          animLoop = true;
          if (frameBuffer != nullptr && crossfadeOldBuf != nullptr && crossfadeNewBuf != nullptr) {
            memcpy(crossfadeOldBuf, frameBuffer, TFT_W * TFT_H * sizeof(uint16_t));
            char nextFilename[32];
            sprintf(nextFilename, "%s/%04d.jpg", animPaths[nextAnim], 1);
            if (displayAnimJPEG(nextFilename, crossfadeNewBuf, false, nextAnim)) {
              crossfadeActive = true;
              crossfadeStartMs = millis();
              currentAnim = nextAnim;
              currentFrame = 2;
            } else {
              currentAnim = nextAnim;
              currentFrame = 1;
            }
          } else {
            currentAnim = nextAnim;
            currentFrame = 1;
          }
        }
      }
    }
  } else {
    notifyExpressionAnimDone();
    startIdleAnimationLoop();
  }
}

void scanAnimationFolders() {
  if (!LOCAL_EXPRESSION_ANIMATION_ENABLED) {
    maxAnimIndex = 0;
    return;
  }
  for (int i = 0; i <= MAX_ANIMS; ++i) {
    animFrames[i] = 0;
    animPaths[i][0] = '\0';
  }
  maxAnimIndex = 0;
  int foundCount = 0;
  for (int i = 1; i <= MAX_ANIMS; ++i) {
    char path[16];
    snprintf(path, sizeof(path), "%s%d", ANIM_PATH_PREFIX, i);
    if (!LittleFS.exists(path)) continue;
    int frames = countAnimFrames(path);
    if (frames <= 0) continue;
    strncpy(animPaths[i], path, sizeof(animPaths[i]) - 1);
    animPaths[i][sizeof(animPaths[i]) - 1] = '\0';
    animFrames[i] = frames;
    if (i > maxAnimIndex) maxAnimIndex = i;
    foundCount++;
    LOGF("[ANIM] found %s (%d frames)\n", path, frames);
  }
  if (foundCount > 0) {
    LOGF("[ANIM] total animations: %d (maxIndex=%d)\n", foundCount, maxAnimIndex);
  } else {
    LOGF("[ANIM] no animation folders found under %s[1..%d]\n", ANIM_PATH_PREFIX, MAX_ANIMS);
  }
}

void setScreenPageMode(ScreenPageMode mode) {
  screenPageMode = mode;
  if (mode == SCREEN_PAGE_EXPRESSION) {
    visionPreviewValid = false;
    expressionDirty = true;
    crossfadeActive = false;
    if (coverInfoMode) {
      currentAnim = 0;
      animLoop = false;
      drawCoverInfoScreen();
    } else if (mouthTalkingMode) {
      startTalkingAnimationLoop();
    } else if (maxAnimIndex > 0) {
      startIdleAnimationLoop();
    } else {
      currentAnim = 0;
      animLoop = false;
      drawCatExpressionScreen(false);
    }
  } else if (mode == SCREEN_PAGE_DASHBOARD) {
    setTftRotationIfNeeded(0);
    drawRemoteScreenWaiting("DASH", "Cat dashboard");
  } else if (mode == SCREEN_PAGE_TRANSLATE) {
    setTftRotationIfNeeded(0);
    drawRemoteScreenWaiting("SIM-TRANS", "Simultaneous interpreter");
  } else if (mode == SCREEN_PAGE_VISION_DIALOG) {
    setTftRotationIfNeeded(0);
    drawRemoteScreenWaiting("VISION", "Visual voice dialogue");
    visionPreviewValid = false;
  } else {
    setTftRotationIfNeeded(0);
    drawRemoteScreenWaiting("CAM", "Host camera");
  }
}

void startTone(uint16_t freq, uint16_t durationMs, uint8_t volumePercent) {
  const int sampleRate = 16000;
  const int samples = (sampleRate * durationMs) / 1000;
  const float gain = constrain(volumePercent, 1, 100) / 100.0f * 0.35f;
  int32_t outBuf[256 * 2];
  float phase = 0.0f;
  const float phaseInc = 2.0f * PI * static_cast<float>(freq) / static_cast<float>(sampleRate);
  for (int offset = 0; offset < samples; offset += 256) {
    int chunk = min(256, samples - offset);
    for (int i = 0; i < chunk; ++i) {
      int16_t sample = static_cast<int16_t>(sinf(phase) * 32767.0f * gain);
      phase += phaseInc;
      int32_t v = static_cast<int32_t>(sample) << 16;
      outBuf[i * 2] = v;
      outBuf[i * 2 + 1] = v;
    }
    i2sOut.write(reinterpret_cast<uint8_t*>(outBuf), chunk * 2 * sizeof(int32_t));
  }
}

bool initCamera() {
  camera_config_t config;
  config.ledc_channel = LEDC_CHANNEL_0;
  config.ledc_timer = LEDC_TIMER_0;
  config.pin_d0 = CAM_PIN_D0;
  config.pin_d1 = CAM_PIN_D1;
  config.pin_d2 = CAM_PIN_D2;
  config.pin_d3 = CAM_PIN_D3;
  config.pin_d4 = CAM_PIN_D4;
  config.pin_d5 = CAM_PIN_D5;
  config.pin_d6 = CAM_PIN_D6;
  config.pin_d7 = CAM_PIN_D7;
  config.pin_xclk = CAM_PIN_XCLK;
  config.pin_pclk = CAM_PIN_PCLK;
  config.pin_vsync = CAM_PIN_VSYNC;
  config.pin_href = CAM_PIN_HREF;
  config.pin_sscb_sda = CAM_PIN_SIOD;
  config.pin_sscb_scl = CAM_PIN_SIOC;
  config.pin_pwdn = CAM_PIN_PWDN;
  config.pin_reset = CAM_PIN_RESET;
  config.xclk_freq_hz = 20000000;
  config.pixel_format = PIXFORMAT_JPEG;
  config.frame_size = CAMERA_FRAME_SIZE;
  config.jpeg_quality = JPEG_QUALITY;
  config.fb_count = psramFound() ? CAMERA_FB_COUNT : 1;
  config.fb_location = psramFound() ? CAMERA_FB_IN_PSRAM : CAMERA_FB_IN_DRAM;
  config.grab_mode = CAMERA_GRAB_LATEST;

  esp_err_t err = esp_camera_init(&config);
  if (err != ESP_OK) {
    LOGF("[CAM] init failed: 0x%x\n", err);
    return false;
  }

  sensor_t* sensor = esp_camera_sensor_get();
  if (sensor) {
    sensor->set_hmirror(sensor, 0);
    sensor->set_vflip(sensor, 1);
    sensor->set_brightness(sensor, 0);
    sensor->set_contrast(sensor, 1);
    sensor->set_saturation(sensor, 0);
    sensor->set_gain_ctrl(sensor, 1);
    sensor->set_exposure_ctrl(sensor, 1);
    sensor->set_whitebal(sensor, 1);
    sensor->set_awb_gain(sensor, 1);
  }
  return true;
}

void initScreen() {
  SPI.begin(TFT_SCLK, -1, TFT_MOSI, TFT_CS);
  tft.init(TFT_W, TFT_H);
  tft.applyOffsets(TFT_COL_OFFSET, TFT_ROW_OFFSET);
  currentTftRotation = 255;
  setTftRotationIfNeeded(0);
  tft.setSPISpeed(TFT_SPI_SPEED);
  tft.invertDisplay(true);
  uint8_t ctrl = 0x2C;
  uint8_t bri = 255;
  tft.sendCommand(0x53, &ctrl, 1);
  tft.sendCommand(0x51, &bri, 1);
  drawStatusBanner("BOOT", "init screen");
}

void initI2SIn() {
  i2sIn.setPinsPdmRx(I2S_MIC_CLOCK_PIN, I2S_MIC_DATA_PIN);
  if (!i2sIn.begin(I2S_MODE_PDM_RX, SAMPLE_RATE, I2S_DATA_BIT_WIDTH_16BIT, I2S_SLOT_MODE_MONO)) {
    LOGLN("[I2S IN] init failed");
  } else {
    LOGLN("[I2S IN] ready");
  }
}

void initI2SOut(int sampleRate = TTS_RATE) {
  i2sOut.end();
  i2sOut.setPins(I2S_SPK_BCLK, I2S_SPK_LRCK, I2S_SPK_DIN);
  if (!i2sOut.begin(I2S_MODE_STD, sampleRate, I2S_DATA_BIT_WIDTH_32BIT, I2S_SLOT_MODE_STEREO)) {
    LOGLN("[I2S OUT] init failed");
  } else {
    LOGF("[I2S OUT] ready @%d\n", sampleRate);
  }
}

static inline void mono16_to_stereo32_msb(const int16_t* input, size_t sampleCount, int32_t* outputLR, float gain = 0.75f) {
  for (size_t i = 0; i < sampleCount; ++i) {
    int32_t sample = static_cast<int32_t>(static_cast<float>(input[i]) * gain);
    sample = constrain(sample, -32768, 32767);
    int32_t packed = sample << 16;
    outputLR[i * 2] = packed;
    outputLR[i * 2 + 1] = packed;
  }
}

void taskHttpPlay(void*) {
  static int32_t outLR[1024 * 2];
  auto streamReadable = [&]() -> bool {
    return ttsClient.connected() || ttsClient.available() > 0;
  };

  auto readLine = [&](String& out, uint32_t timeoutMs) -> bool {
    out = "";
    uint32_t start = millis();
    while (millis() - start < timeoutMs) {
      while (ttsClient.available()) {
        char c = static_cast<char>(ttsClient.read());
        if (c == '\r') continue;
        if (c == '\n') return true;
        out += c;
        if (out.length() > 1024) return false;
      }
      delay(1);
    }
    return false;
  };

  auto readNRaw = [&](uint8_t* dst, size_t need, uint32_t timeoutMs) -> bool {
    size_t got = 0;
    uint32_t start = millis();
    while (got < need) {
      int avail = ttsClient.available();
      if (avail > 0) {
        int take = min(avail, static_cast<int>(need - got));
        int readCount = ttsClient.read(dst + got, take);
        if (readCount > 0) {
          got += readCount;
          continue;
        }
      }
      if (!streamReadable()) return false;
      if (millis() - start > timeoutMs) return false;
      delay(1);
    }
    return true;
  };

  LOGLN("[TTS] task ready");
  while (WiFi.status() != WL_CONNECTED) {
    delay(100);
  }

  while (true) {
    if (!ttsAudioAvailable) {
      vTaskDelay(pdMS_TO_TICKS(80));
      continue;
    }

    ttsClient.stop();
    if (!ttsClient.connect(SERVER_HOST, SERVER_PORT)) {
      ttsAudioAvailable = false;
      micEnabled = true;
      delay(300);
      continue;
    }
    String request = String("GET /stream.wav HTTP/1.1\r\n") +
                     "Host: " + SERVER_HOST + ":" + String(SERVER_PORT) + "\r\n" +
                     "Connection: close\r\n\r\n";
    ttsClient.print(request);

    bool headerOk = false;
    bool isChunked = false;
    uint32_t chunkLeft = 0;
    String line;
    while (readLine(line, 1200)) {
      String lower = line;
      lower.toLowerCase();
      if (lower.startsWith("transfer-encoding:") && lower.indexOf("chunked") >= 0) {
        isChunked = true;
      }
      if (line.length() == 0) {
        headerOk = true;
        break;
      }
    }
    if (!headerOk) {
      ttsClient.stop();
      ttsAudioAvailable = false;
      continue;
    }

    auto readBody = [&](uint8_t* dst, size_t need, uint32_t timeoutMs) -> bool {
      size_t filled = 0;
      uint32_t start = millis();
      while (filled < need) {
        if (!streamReadable()) return false;
        if (isChunked) {
          if (chunkLeft == 0) {
            String chunkLine;
            if (!readLine(chunkLine, timeoutMs)) return false;
            int semicolon = chunkLine.indexOf(';');
            if (semicolon >= 0) chunkLine = chunkLine.substring(0, semicolon);
            chunkLine.trim();
            uint32_t sz = 0;
            if (sscanf(chunkLine.c_str(), "%x", &sz) != 1) return false;
            if (sz == 0) {
              String dummy;
              readLine(dummy, 200);
              return false;
            }
            chunkLeft = sz;
          }
          size_t piece = min(static_cast<size_t>(chunkLeft), need - filled);
          if (!readNRaw(dst + filled, piece, timeoutMs)) return false;
          filled += piece;
          chunkLeft -= piece;
          if (chunkLeft == 0) {
            uint8_t crlf[2];
            if (!readNRaw(crlf, 2, 200)) return false;
          }
        } else {
          if (!readNRaw(dst + filled, need - filled, timeoutMs)) return false;
          filled = need;
        }
        if (millis() - start > timeoutMs) return false;
      }
      return true;
    };

    uint8_t riff[12];
    if (!readBody(riff, 12, 1200) || memcmp(riff, "RIFF", 4) != 0 || memcmp(riff + 8, "WAVE", 4) != 0) {
      ttsClient.stop();
      ttsAudioAvailable = false;
      micEnabled = true;
      continue;
    }

    bool gotFmt = false;
    bool gotData = false;
    uint16_t numChannels = 0;
    uint16_t bitsPerSample = 0;
    uint32_t sampleRate = 0;
    uint8_t chunkHeader[8];

    while (!gotData) {
      if (!readBody(chunkHeader, 8, 1200)) {
        ttsClient.stop();
        break;
      }
      uint32_t chunkSize = static_cast<uint32_t>(chunkHeader[4]) |
                           (static_cast<uint32_t>(chunkHeader[5]) << 8) |
                           (static_cast<uint32_t>(chunkHeader[6]) << 16) |
                           (static_cast<uint32_t>(chunkHeader[7]) << 24);

      if (memcmp(chunkHeader, "fmt ", 4) == 0) {
        uint8_t fmtBuf[32];
        size_t readSize = min<size_t>(chunkSize, sizeof(fmtBuf));
        if (!readBody(fmtBuf, readSize, 1200)) {
          ttsClient.stop();
          break;
        }
        if (chunkSize > readSize) {
          uint8_t dump[128];
          size_t left = chunkSize - readSize;
          while (left > 0) {
            size_t take = min<size_t>(left, sizeof(dump));
            if (!readBody(dump, take, 1200)) {
              ttsClient.stop();
              break;
            }
            left -= take;
          }
        }
        numChannels = static_cast<uint16_t>(fmtBuf[2] | (fmtBuf[3] << 8));
        sampleRate = static_cast<uint32_t>(fmtBuf[4]) |
                     (static_cast<uint32_t>(fmtBuf[5]) << 8) |
                     (static_cast<uint32_t>(fmtBuf[6]) << 16) |
                     (static_cast<uint32_t>(fmtBuf[7]) << 24);
        bitsPerSample = static_cast<uint16_t>(fmtBuf[14] | (fmtBuf[15] << 8));
        gotFmt = true;
      } else if (memcmp(chunkHeader, "data", 4) == 0) {
        if (!gotFmt) {
          ttsClient.stop();
          break;
        }
        gotData = true;
      } else {
        uint8_t dump[128];
        size_t left = chunkSize;
        while (left > 0) {
          size_t take = min<size_t>(left, sizeof(dump));
          if (!readBody(dump, take, 1200)) {
            ttsClient.stop();
            break;
          }
          left -= take;
        }
      }
    }

    if (!gotData) {
      ttsAudioAvailable = false;
      micEnabled = true;
      continue;
    }

    if (!(numChannels == 1 && bitsPerSample == 16 &&
          (sampleRate == 8000 || sampleRate == 12000 || sampleRate == 16000 || sampleRate == 24000))) {
      LOGF("[TTS] unsupported fmt ch=%u bits=%u sr=%u\n", numChannels, bitsPerSample, sampleRate);
      ttsClient.stop();
      ttsAudioAvailable = false;
      micEnabled = true;
      continue;
    }

    initI2SOut(static_cast<int>(sampleRate));
    ttsPlaying = true;
    micEnabled = false;
    resetMouthState();

    while (ttsAudioAvailable && streamReadable()) {
      uint8_t inBuf[2048];
      uint32_t bytes20 = (sampleRate * 2 * 20) / 1000;
      if (bytes20 < 2) bytes20 = 2;
      if (!readBody(inBuf, bytes20, 1500)) {
        break;
      }
      size_t filled = bytes20;
      while (filled + bytes20 <= sizeof(inBuf)) {
        if (!readBody(inBuf + filled, bytes20, 4)) break;
        filled += bytes20;
      }
      if (filled & 1) filled -= 1;
      if (filled == 0) continue;
      size_t sampleCount = filled / 2;
      updateMouthFromAudioChunk(reinterpret_cast<int16_t*>(inBuf), sampleCount);
      mono16_to_stereo32_msb(reinterpret_cast<int16_t*>(inBuf), sampleCount, outLR,
                            static_cast<float>(speakerVolumePercent) / 100.0f);
      size_t totalBytes = sampleCount * 2 * sizeof(int32_t);
      size_t offset = 0;
      while (offset < totalBytes) {
        size_t wrote = i2sOut.write(reinterpret_cast<uint8_t*>(outLR) + offset, totalBytes - offset);
        if (wrote == 0) {
          vTaskDelay(pdMS_TO_TICKS(1));
        } else {
          offset += wrote;
        }
      }
    }

    ttsPlaying = false;
    resetMouthState();
    ttsAudioAvailable = false;
    micEnabled = true;
    ttsClient.stop();
    initI2SOut(TTS_RATE);
  }
}

float easeServoProgress(float t) {
  t = constrain(t, 0.0f, 1.0f);
  // smootherstep：和旧版 smoothstep 一样是常数复杂度，但起步/收尾更柔和，
  // 在不增加插值点数量的前提下，让动作观感更丝滑。
  return t * t * t * (t * (t * 6.0f - 15.0f) + 10.0f);
}

int interpInt(int startValue, int endValue, float t) {
  return static_cast<int>(lroundf(startValue + (endValue - startValue) * t));
}

void moveCatPoseSmooth(int yaw, int pitch, int ear, int durationMs) {
  // 全局进一步限速：把业务层 duration 放大到 1.6 倍，并抬高最小动作时长。
  // 这样说话、表情、待机和跟随期间的动作都会更慢更稳。
  // 所有调用方（idle 动作、表情预设、翻译期间的随机动作、手势跟随）都经过这里。
  durationMs = (durationMs * SERVO_DURATION_SCALE_NUM) / SERVO_DURATION_SCALE_DEN;
  yaw = clampServoLogicalAngle(HEAD_YAW_INDEX, yaw);
  pitch = clampServoLogicalAngle(HEAD_PITCH_INDEX, pitch);
  ear = clampEarLogicalAngle(ear);
  int startYaw = currentServoAngles[HEAD_YAW_INDEX];
  int startPitch = currentServoAngles[HEAD_PITCH_INDEX];
  int startEar = logicalEarAngle;
  int safeDuration = constrain(durationMs, SERVO_MIN_MOVE_MS, 3000);
  int moveSpan = abs(yaw - startYaw);
  moveSpan = max(moveSpan, abs(pitch - startPitch) * 2);
  moveSpan = max(moveSpan, abs(ear - startEar) / 2);
  int steps = 4 + moveSpan / 7 + safeDuration / 260;
  steps = constrain(steps, 4, 11);
  int stepDelay = safeDuration / max(1, steps);
  if (stepDelay < 20) stepDelay = 20;

  for (int i = 1; i <= steps; ++i) {
    if (poseAbortRequested || audioPriorityModeActive()) return;
    float t = static_cast<float>(i) / static_cast<float>(steps);
    float eased = easeServoProgress(t);
    applyCatPose(interpInt(startYaw, yaw, eased), interpInt(startPitch, pitch, eased), interpInt(startEar, ear, eased));
    expressionDirty = true;
    vTaskDelay(pdMS_TO_TICKS(stepDelay));
  }
  applyCatPose(yaw, pitch, ear);
  expressionDirty = true;
}

int stepTowardInt(int currentValue, int targetValue, int maxStep) {
  int delta = targetValue - currentValue;
  if (delta > maxStep) return currentValue + maxStep;
  if (delta < -maxStep) return currentValue - maxStep;
  return targetValue;
}

void activateDirectPoseControl() {
  directTargetYaw = currentServoAngles[HEAD_YAW_INDEX];
  directTargetPitch = currentServoAngles[HEAD_PITCH_INDEX];
  directTargetEar = logicalEarAngle;
  directPoseActive = true;
  lastDirectServoUpdateMs = 0;
}

void serviceDirectPoseControl() {
  if (!directPoseActive || poseMotionActive || audioPriorityModeActive()) return;
  uint32_t now = millis();
  if (now - lastDirectServoUpdateMs < DIRECT_SERVO_UPDATE_INTERVAL_MS) return;
  lastDirectServoUpdateMs = now;

  int nextYaw = stepTowardInt(currentServoAngles[HEAD_YAW_INDEX],
                              clampServoLogicalAngle(HEAD_YAW_INDEX, directTargetYaw),
                              DIRECT_SERVO_MAX_STEP_DEG);
  int nextPitch = stepTowardInt(currentServoAngles[HEAD_PITCH_INDEX],
                                clampServoLogicalAngle(HEAD_PITCH_INDEX, directTargetPitch),
                                DIRECT_SERVO_MAX_STEP_DEG);
  int nextEar = stepTowardInt(logicalEarAngle, clampEarLogicalAngle(directTargetEar), DIRECT_EAR_MAX_STEP_DEG);

  if (nextYaw == currentServoAngles[HEAD_YAW_INDEX] &&
      nextPitch == currentServoAngles[HEAD_PITCH_INDEX] &&
      nextEar == logicalEarAngle) {
    return;
  }

  applyCatPose(nextYaw, nextPitch, nextEar);
  expressionDirty = true;
}

void taskCatPoseDriver(void*) {
  LOGLN("[POSE] task ready");
  for (;;) {
    CatPoseFrame frame;
    if (xQueueReceive(qCatPoses, &frame, pdMS_TO_TICKS(100)) == pdPASS) {
      poseMotionActive = true;
      moveCatPoseSmooth(frame.yaw, frame.pitch, frame.ear, frame.durationMs);
      poseMotionActive = false;
      if (poseAbortRequested) {
        poseAbortRequested = false;
      }
    }
  }
}

bool isUniformCameraFrame(camera_fb_t* fb) {
  if (fb == nullptr || fb->buf == nullptr || fb->len == 0) return false;
  if (fb->len > COVER_JPEG_SIZE_THRESHOLD) return false;
  size_t start = min<size_t>(512, fb->len / 8);
  size_t end = min(fb->len, start + 4096);
  if (end <= start + 64) return fb->len <= COVER_JPEG_SIZE_THRESHOLD;

  uint8_t minValue = 255;
  uint8_t maxValue = 0;
  uint32_t transitions = 0;
  uint32_t samples = 0;
  uint8_t prev = 0;
  bool hasPrev = false;
  for (size_t i = start; i < end; i += 64) {
    uint8_t v = fb->buf[i];
    if (v < minValue) minValue = v;
    if (v > maxValue) maxValue = v;
    if (hasPrev && abs(static_cast<int>(v) - static_cast<int>(prev)) > 28) {
      transitions++;
    }
    prev = v;
    hasPrev = true;
    samples++;
  }
  return (maxValue - minValue) < 96 && transitions < (samples / 2 + 1);
}

void toggleCoverInfoMode() {
  if (screenPageMode != SCREEN_PAGE_EXPRESSION) return;
  coverInfoMode = !coverInfoMode;
  lastCoverToggleMs = millis();
  expressionDirty = true;
  crossfadeActive = false;
  noteInteraction();
  if (!coverInfoMode && maxAnimIndex > 0) {
    startIdleAnimationLoop();
  }
}

void updateCameraCoverState(camera_fb_t* fb) {
  if (screenPageMode != SCREEN_PAGE_EXPRESSION) return;
  bool uniformFrame = isUniformCameraFrame(fb);
  if (uniformFrame) {
    coverDetectedFrames = min<uint8_t>(coverDetectedFrames + 1, 10);
    coverClearFrames = 0;
    if (!cameraCoverStable && coverDetectedFrames >= COVER_STABLE_FRAMES) {
      cameraCoverStable = true;
      if (coverToggleArmed && millis() - lastCoverToggleMs >= COVER_TOGGLE_DEBOUNCE_MS) {
        coverToggleArmed = false;
        toggleCoverInfoMode();
      }
    }
  } else {
    coverClearFrames = min<uint8_t>(coverClearFrames + 1, 10);
    coverDetectedFrames = 0;
    if (cameraCoverStable && coverClearFrames >= COVER_STABLE_FRAMES) {
      cameraCoverStable = false;
      coverToggleArmed = true;
    }
  }
}

void taskCamCapture(void*) {
  uint32_t lastCaptureMs = 0;
  for (;;) {
    int uploadFps = camWsReady ? effectiveCameraFps() : 0;
    bool localVisionPreview = screenPageMode == SCREEN_PAGE_VISION_DIALOG && visionPreviewBuffer != nullptr;
    int activeFps = camWsReady ? uploadFps : COVER_DETECTION_FPS;
    if (localVisionPreview) {
      // 图文排版屏的摄像头预览在 ESP32 本地绘制，不依赖 server 是否要求上传帧。
      activeFps = max(activeFps, VISION_LOCAL_PREVIEW_FPS);
    }
    if (activeFps <= 0) {
      vTaskDelay(pdMS_TO_TICKS(40));
      continue;
    }
    uint32_t intervalMs = max(80, 1000 / activeFps);
    if (millis() - lastCaptureMs >= intervalMs) {
      camera_fb_t* fb = esp_camera_fb_get();
      if (fb && fb->format == PIXFORMAT_JPEG) {
        lastCaptureMs = millis();
        updateCameraCoverState(fb);
        drawLocalVisionCameraPreview(fb);
        if (camWsReady && uploadFps > 0) {
          if (xQueueSend(qFrames, &fb, 0) != pdPASS) {
            fb_ptr_t drop = nullptr;
            if (xQueueReceive(qFrames, &drop, 0) == pdPASS && drop) {
              esp_camera_fb_return(drop);
            }
            xQueueSend(qFrames, &fb, 0);
          }
        } else {
          esp_camera_fb_return(fb);
        }
      } else if (fb) {
        esp_camera_fb_return(fb);
      }
    }
    vTaskDelay(pdMS_TO_TICKS(10));
  }
}

void taskCamSend(void*) {
  for (;;) {
    fb_ptr_t fb = nullptr;
    if (xQueueReceive(qFrames, &fb, pdMS_TO_TICKS(100)) == pdPASS) {
      if (fb && camWsReady) {
        bool ok = wsCam.sendBinary(reinterpret_cast<const char*>(fb->buf), fb->len);
        if (!ok) {
          wsCam.close();
          camWsReady = false;
        }
        esp_camera_fb_return(fb);
      } else if (fb) {
        esp_camera_fb_return(fb);
      }
    }
  }
}

void taskMicCapture(void*) {
  const int samplesPerChunk = BYTES_PER_CHUNK / 2;
  LOGLN("[MIC] capture ready");
  for (;;) {
    if (runAudioStream && audWsReady && micEnabled && !ttsPlaying) {
      AudioChunk chunk;
      chunk.n = BYTES_PER_CHUNK;
      int16_t* out = reinterpret_cast<int16_t*>(chunk.data);
      int i = 0;
      int retry = 0;
      while (i < samplesPerChunk && retry < 1000) {
        int value = i2sIn.read();
        if (value == -1) {
          delay(1);
          retry++;
          continue;
        }
        out[i++] = static_cast<int16_t>(value);
        retry = 0;
      }
      if (i == samplesPerChunk) {
        if (xQueueSend(qAudio, &chunk, 0) != pdPASS) {
          AudioChunk dump;
          xQueueReceive(qAudio, &dump, 0);
          xQueueSend(qAudio, &chunk, 0);
        }
      }
    } else {
      vTaskDelay(pdMS_TO_TICKS(10));
    }
  }
}

void taskMicUpload(void*) {
  LOGLN("[MIC] upload ready");
  for (;;) {
    if (runAudioStream && audWsReady && !ttsPlaying) {
      AudioChunk chunk;
      if (xQueueReceive(qAudio, &chunk, pdMS_TO_TICKS(100)) == pdPASS) {
        wsAud.sendBinary(reinterpret_cast<const char*>(chunk.data), chunk.n);
      }
    } else {
      vTaskDelay(pdMS_TO_TICKS(10));
    }
  }
}

bool autoIdleAllowed() {
  if (rawPwmTestMode) return false;
  if (coverInfoMode) return false;
  // 允许表情页和同声传译页都触发 idle 动作，让猫在等译文的间隙也"活着"。
  if (screenPageMode != SCREEN_PAGE_EXPRESSION && screenPageMode != SCREEN_PAGE_TRANSLATE) return false;
  if (ttsPlaying || ttsAudioAvailable) return false;
  if (poseMotionActive) return false;
  if (qCatPoses != nullptr && uxQueueMessagesWaiting(qCatPoses) > 0) return false;
  return true;
}

uint8_t chooseIdleActionId() {
  // 提高俯仰类 idle（peek up / double tilt / nod / curious）的出现频率，
  // 但不额外放大俯仰角度本身。
  static const uint8_t kWeightedIdleActionIds[] = {
      11, 12, 13, 14,
      15, 15, 15,
      16,
      17, 17, 17,
      18,
      19, 19, 19,
      20, 20, 20,
  };
  constexpr size_t kIdleChoiceCount = sizeof(kWeightedIdleActionIds) / sizeof(kWeightedIdleActionIds[0]);
  return kWeightedIdleActionIds[random(kIdleChoiceCount)];
}

void scheduleIdleActionIfNeeded() {
  if (!autoIdleAllowed()) return;
  uint32_t now = millis();
  if (nextIdleActionMs == 0) nextIdleActionMs = now + IDLE_ACTION_START_DELAY_MS;
  if (now < nextIdleActionMs) return;

  if (random(100) < 60) {
    nextIdleActionMs = now + random(IDLE_STATIC_MIN_MS, IDLE_STATIC_MAX_MS + 1);
    return;
  }

  uint8_t idleActionId = chooseIdleActionId();
  uint32_t duration = queueBuiltinAction(idleActionId, true);
  if (duration < 300) duration = 300;
  nextIdleActionMs = now + duration + random(IDLE_ACTION_GAP_MIN_MS, IDLE_ACTION_GAP_MAX_MS + 1);
}

void applyExpressionPreset(const String& emotion) {
  clearPoseQueue(true);
  int actionId = emotionToBuiltinActionId(emotion);
  if (actionId > 0) {
    queueBuiltinAction(static_cast<uint8_t>(actionId), false);
  } else {
    queueCatPose(90, 90, 90, 180);
  }
}

void handleCameraSetting(const String& param, int value) {
  sensor_t* sensor = esp_camera_sensor_get();
  if (!sensor) return;
  if (param == "brightness") {
    sensor->set_brightness(sensor, constrain(value, -2, 2));
  } else if (param == "contrast") {
    sensor->set_contrast(sensor, constrain(value, -2, 2));
  } else if (param == "saturation") {
    sensor->set_saturation(sensor, constrain(value, -2, 2));
  } else if (param == "sharpness") {
    sensor->set_sharpness(sensor, constrain(value, -2, 2));
  } else if (param == "wb_mode") {
    sensor->set_wb_mode(sensor, constrain(value, 0, 4));
  } else if (param == "ae_level") {
    sensor->set_ae_level(sensor, constrain(value, -2, 2));
  } else if (param == "special_effect") {
    sensor->set_special_effect(sensor, constrain(value, 0, 6));
  } else if (param == "hmirror") {
    sensor->set_hmirror(sensor, value ? 1 : 0);
  } else if (param == "vflip") {
    sensor->set_vflip(sensor, value ? 1 : 0);
  }
}

void handleTftSetting(const String& param, int value) {
  if (param == "brightness") {
    uint8_t ctrl = 0x2C;
    tft.sendCommand(0x53, &ctrl, 1);
    uint8_t bri = static_cast<uint8_t>(constrain(value, 0, 255));
    tft.sendCommand(0x51, &bri, 1);
  } else if (param == "invert") {
    tft.invertDisplay(value != 0);
  } else if (param == "gamma") {
    uint8_t gamma = static_cast<uint8_t>(constrain(value, 1, 8));
    tft.sendCommand(0x26, &gamma, 1);
  } else if (param == "display") {
    tft.enableDisplay(value != 0);
  } else if (param == "rotation") {
    setTftRotationIfNeeded(constrain(value, 0, 3));
  } else if (param == "cabc") {
    uint8_t cabc = static_cast<uint8_t>(constrain(value, 0, 3));
    tft.sendCommand(0x55, &cabc, 1);
  }
}

void handleAudioSetting(const String& param, int value) {
  if (param == "volume") {
    speakerVolumePercent = static_cast<uint8_t>(constrain(value, 0, 100));
    LOGF("[AUDIO] speaker volume=%u%%\n", speakerVolumePercent);
  }
}

void setupWebSocketCallbacks() {
  wsCam.onEvent([](WebsocketsEvent event, String) {
    if (event == WebsocketsEvent::ConnectionOpened) {
      camWsReady = true;
      LOGLN("[WS-CAM] connected");
    } else if (event == WebsocketsEvent::ConnectionClosed) {
      camWsReady = false;
      LOGLN("[WS-CAM] disconnected");
    }
  });

  wsCam.onMessage([](WebsocketsMessage msg) {
    if (msg.isBinary()) {
      if (screenPageMode != SCREEN_PAGE_EXPRESSION) {
        updateRemoteScreenFrame(reinterpret_cast<const uint8_t*>(msg.c_str()), msg.length());
      }
      return;
    }
    if (!msg.isText()) return;
    String s = msg.data();
    s.trim();
    if (s.startsWith("SCRMODE:")) {
      setScreenPageMode(static_cast<ScreenPageMode>(constrain(s.substring(8).toInt(), 0, 4)));
    } else if (s.startsWith("SET:FPS=")) {
      int fps = static_cast<int>(s.substring(8).toInt());
      gTargetFps = (fps > 0) ? fps : 0;
    }
  });

  wsAud.onEvent([](WebsocketsEvent event, String) {
    if (event == WebsocketsEvent::ConnectionOpened) {
      audWsReady = true;
      LOGLN("[WS-AUD] connected");
      runAudioStream = true;
      micEnabled = true;
      wsAud.send("START");
    } else if (event == WebsocketsEvent::ConnectionClosed) {
      audWsReady = false;
      runAudioStream = false;
      LOGLN("[WS-AUD] disconnected");
    }
  });

  wsAud.onMessage([](WebsocketsMessage msg) {
    if (!msg.isText()) return;
    String s = msg.data();
    s.trim();

    if (s == "START") {
      runAudioStream = true;
      micEnabled = true;
      if (qAudio) xQueueReset(qAudio);
      noteInteraction();
      wsAud.send("OK:STARTED");
      return;
    }
    if (s == "STOP") {
      runAudioStream = false;
      wsAud.send("OK:STOPPED");
      return;
    }
    if (s == "RESTART") {
      runAudioStream = false;
      if (qAudio) xQueueReset(qAudio);
      delay(60);
      runAudioStream = true;
      noteInteraction();
      wsAud.send("START");
      return;
    }
    if (s == "RESET") {
      runAudioStream = false;
      micEnabled = false;
      ttsAudioAvailable = false;
      ttsPlaying = false;
      // 异常/复位路径也确保 WiFi TX 功率恢复到正常档位，避免卡在低功率
      WiFi.setTxPower(static_cast<wifi_power_t>(WIFI_TX_POWER_NORMAL_DBM * 4));
      expressionAnimNotifyPending = false;
      expressionAnimNotifyToken = "";
      stopTalkingAnimationLoop();
      if (qAudio) xQueueReset(qAudio);
      clearPoseQueue(true);
      centerCatPose();
      currentEmotion = "neutral";
      expressionDirty = true;
      currentAnim = 0;
      animLoop = false;
      crossfadeActive = false;
      noteInteraction();
      return;
    }
    if (s == "TTS_START") {
      runAudioStream = false;
      micEnabled = false;
      clearPoseQueue(true);
      directPoseActive = false;
      // TTS 播放前让 WiFi 发射的最后一点尾流先结束，再拉起功放电流，避免
      // WiFi TX 峰值 + I2S 功放启动瞬态叠加把电池电压拉穿。这里只是 2ms 的空等，
      // 不影响音频延迟感知。
      delay(2);
      WiFi.setTxPower(static_cast<wifi_power_t>(WIFI_TX_POWER_TTS_DBM * 4));
      ttsAudioAvailable = true;
      expressionAnimNotifyPending = false;
      expressionAnimNotifyToken = "";
      startTalkingAnimationLoop();
      noteInteraction();
      return;
    }
    if (s == "TTS_STOP") {
      ttsPlaying = false;
      ttsAudioAvailable = false;
      // 恢复 WiFi 发射功率到正常档位
      WiFi.setTxPower(static_cast<wifi_power_t>(WIFI_TX_POWER_NORMAL_DBM * 4));
      micEnabled = true;
      expressionAnimNotifyPending = false;
      expressionAnimNotifyToken = "";
      stopTalkingAnimationLoop();
      noteInteraction();
      return;
    }
    if (s.startsWith("SCRMODE:")) {
      setScreenPageMode(static_cast<ScreenPageMode>(constrain(s.substring(8).toInt(), 0, 4)));
      noteInteraction();
      return;
    }
    if (s.startsWith("SET:FPS=")) {
      int fps = static_cast<int>(s.substring(8).toInt());
      gTargetFps = (fps > 0) ? fps : 0;
      return;
    }
    if (audioPriorityModeActive() &&
        (s.startsWith("SERVO:") || s == "PWMTEST:ON" || s == "PWMTEST:OFF" || s.startsWith("PWMRAW:") ||
         s.startsWith("EARS:") || s.startsWith("CATPOSE:") || s.startsWith("ACTION:") ||
         s.startsWith("EXPRSEQ:") || s.startsWith("EXPR:") || s.startsWith("ANIM:"))) {
      return;
    }
    if (s.startsWith("SERVO:")) {
      String params = s.substring(6);
      int comma = params.indexOf(',');
      if (comma > 0) {
        int ch = params.substring(0, comma).toInt();
        int angle = params.substring(comma + 1).toInt();
        if (ch >= 0 && ch < SERVO_COUNT) {
          clearPoseQueue(true);
          if (ch == HEAD_YAW_INDEX || ch == HEAD_PITCH_INDEX) {
            activateDirectPoseControl();
            if (ch == HEAD_YAW_INDEX) {
              directTargetYaw = clampServoLogicalAngle(HEAD_YAW_INDEX, angle);
            } else {
              directTargetPitch = clampServoLogicalAngle(HEAD_PITCH_INDEX, angle);
            }
          } else {
            writeServoLogical(ch, angle);
          }
          expressionDirty = true;
          noteInteraction();
        }
      }
      return;
    }
    if (s == "PWMTEST:ON") {
      setRawPwmTestMode(true, false);
      return;
    }
    if (s == "PWMTEST:OFF") {
      setRawPwmTestMode(false, true);
      return;
    }
    if (s.startsWith("PWMRAW:")) {
      String params = s.substring(7);
      int comma = params.indexOf(',');
      if (comma > 0) {
        int ch = params.substring(0, comma).toInt();
        int angle = params.substring(comma + 1).toInt();
        if (ch >= 0 && ch < SERVO_COUNT) {
          if (!rawPwmTestMode) {
            setRawPwmTestMode(true, false);
          }
          writeServoRawPhysical(ch, angle);
          noteInteraction();
          expressionDirty = true;
        }
      }
      return;
    }
    if (s.startsWith("EARS:")) {
      clearPoseQueue(true);
      int angle = s.substring(5).toInt();
      // 双耳滑杆保持独立控制，避免复用 directPose 追目标逻辑时把当前头部姿态也一起带入，
      // 造成“调耳朵时像在带着脖子左右动”的体感。
      directPoseActive = false;
      writeMirroredEars(angle);
      expressionDirty = true;
      noteInteraction();
      return;
    }
    if (s.startsWith("CATPOSE:CLEAR")) {
      clearPoseQueue(true);
      noteInteraction();
      return;
    }
    if (s.startsWith("CATPOSE:")) {
      int yaw, pitch, ear, dur;
      if (sscanf(s.substring(8).c_str(), "%d,%d,%d,%d", &yaw, &pitch, &ear, &dur) == 4) {
        queueCatPose(yaw, pitch, ear, dur);
        noteInteraction();
      }
      return;
    }
    if (s.startsWith("ACTION:")) {
      String params = s.substring(7);
      params.trim();
      if (params == "STOP") {
        clearPoseQueue(true);
      } else {
        int actionId = params.toInt();
        if (actionId >= 1 && actionId <= BUILTIN_ACTION_COUNT) {
          queueBuiltinAction(static_cast<uint8_t>(actionId), true);
          LOGF("[ACTION] play %d %s\n", actionId, BUILTIN_ACTIONS[actionId - 1].name);
        }
      }
      noteInteraction();
      return;
    }
    if (s.startsWith("EMO:")) {
      currentEmotion = s.substring(4);
      expressionDirty = true;
      noteInteraction();
      return;
    }
    if (s.startsWith("EXPRSEQ:")) {
      int split = s.indexOf(':', 8);
      if (split > 8) {
        expressionAnimNotifyToken = s.substring(8, split);
        expressionAnimNotifyPending = expressionAnimNotifyToken.length() > 0;
        currentEmotion = s.substring(split + 1);
        currentEmotion.toLowerCase();
        expressionDirty = true;
        applyExpressionPreset(currentEmotion);
        noteInteraction();
        if (LOCAL_EXPRESSION_ANIMATION_ENABLED && screenPageMode == SCREEN_PAGE_EXPRESSION) {
          int exprAnim = expressionToAnim(currentEmotion);
          if (exprAnim > 0) {
            startExpressionAnimation(exprAnim);
          } else {
            notifyExpressionAnimDone();
          }
        } else {
          notifyExpressionAnimDone();
        }
      }
      return;
    }
    if (s.startsWith("EXPR:")) {
      expressionAnimNotifyPending = false;
      expressionAnimNotifyToken = "";
      currentEmotion = s.substring(5);
      currentEmotion.toLowerCase();
      expressionDirty = true;
      applyExpressionPreset(currentEmotion);
      noteInteraction();
      if (LOCAL_EXPRESSION_ANIMATION_ENABLED && screenPageMode == SCREEN_PAGE_EXPRESSION) {
        int exprAnim = expressionToAnim(currentEmotion);
        if (exprAnim > 0) {
          startExpressionAnimation(exprAnim);
        }
      }
      return;
    }
    if (s.startsWith("ANIM:")) {
      if (!LOCAL_EXPRESSION_ANIMATION_ENABLED) {
        return;
      }
      String params = s.substring(5);
      if (params == "STOP") {
        startIdleAnimationLoop();
      } else {
        int animId = params.toInt();
        if (animId >= 1 && animId <= maxAnimIndex && animFrames[animId] > 0) {
          currentAnim = animId;
          currentFrame = 1;
          animLoop = false;
          lastFrameTime = 0;
          crossfadeActive = false;
        }
      }
      noteInteraction();
      return;
    }
    if (s.startsWith("CAMSET:")) {
      String params = s.substring(7);
      int comma = params.indexOf(',');
      if (comma > 0) {
        handleCameraSetting(params.substring(0, comma), params.substring(comma + 1).toInt());
      }
      return;
    }
    if (s.startsWith("TFTSET:")) {
      String params = s.substring(7);
      int comma = params.indexOf(',');
      if (comma > 0) {
        handleTftSetting(params.substring(0, comma), params.substring(comma + 1).toInt());
      }
      return;
    }
    if (s.startsWith("AUDIOSET:")) {
      String params = s.substring(9);
      int comma = params.indexOf(',');
      if (comma > 0) {
        handleAudioSetting(params.substring(0, comma), params.substring(comma + 1).toInt());
      }
      return;
    }
    if (s.startsWith("AUDIO_TEST:")) {
      int freq = 0;
      int dur = 0;
      if (sscanf(s.substring(11).c_str(), "%d,%d", &freq, &dur) == 2) {
        startTone(constrain(freq, 100, 4000), constrain(dur, 50, 2000), 30);
      }
      return;
    }
  });
}

void connectWiFi() {
  WiFi.mode(WIFI_STA);
  WiFi.setSleep(false);
  lastWiFiRetryMs = millis();
  lastWiFiStatus = WiFi.status();
  LOGF("[WIFI] connect request sent to %s\n", WIFI_SSID);
  WiFi.begin(WIFI_SSID, WIFI_PASS);
}

void maintainWiFi() {
  wl_status_t status = WiFi.status();
  if (status != lastWiFiStatus) {
    lastWiFiStatus = status;
    if (status == WL_CONNECTED) {
      LOGLN("[WIFI] connected: " + ipToString(WiFi.localIP()));
      startTone(262, 120, 24);
      expressionDirty = true;
    } else {
      LOGF("[WIFI] status=%d\n", static_cast<int>(status));
      expressionDirty = true;
    }
  }

  if (status == WL_CONNECTED) return;
  uint32_t now = millis();
  if (now - lastWiFiRetryMs >= WIFI_RETRY_INTERVAL_MS) {
    WiFi.disconnect(false, false);
    connectWiFi();
  }
}

void maintainSockets() {
  uint32_t now = millis();
  if (WiFi.status() != WL_CONNECTED) return;

  if (!wsCam.available() && now - lastCamRetryMs >= 2000) {
    lastCamRetryMs = now;
    if (wsCam.connect(SERVER_HOST, SERVER_PORT, CAM_WS_PATH)) {
      LOGLN("[WS-CAM] connect request sent");
    }
  }

  if (!wsAud.available() && now - lastAudRetryMs >= 3000) {
    lastAudRetryMs = now;
    if (wsAud.connect(SERVER_HOST, SERVER_PORT, AUD_WS_PATH)) {
      LOGLN("[WS-AUD] connect request sent");
    }
  }

  wsCam.poll();
  wsAud.poll();
}

void refreshExpressionScreenIfNeeded() {
  if (screenPageMode != SCREEN_PAGE_EXPRESSION) return;
  uint32_t now = millis();
  if (coverInfoMode) {
    if (expressionDirty || now - lastExpressionRefreshMs > 1000) {
      drawCoverInfoScreen();
      lastExpressionRefreshMs = now;
      expressionDirty = false;
    }
    return;
  }
  if (maxAnimIndex > 0 && LOCAL_EXPRESSION_ANIMATION_ENABLED) {
    setTftRotationIfNeeded(0);
    if (updateCrossfade()) {
      lastFrameTime = now;
      return;
    }
    if (currentAnim < 1 || currentAnim > maxAnimIndex || animFrames[currentAnim] == 0) {
      startIdleAnimationLoop();
    }
    if (now - lastFrameTime >= FRAME_DELAY_MS) {
      playAnimationFrame();
      lastFrameTime = now;
    }
    expressionDirty = false;
    return;
  }
  if (expressionDirty || now - lastExpressionRefreshMs > 3000) {
    expressionBlink = !expressionBlink;
    drawCatExpressionScreen(expressionBlink);
    lastExpressionRefreshMs = now;
    expressionDirty = false;
  }
}

void setup() {
  Serial.begin(115200);
  delay(300);
  randomSeed(esp_random());

  jpegBuffer = static_cast<uint8_t*>(psramFound() ? ps_malloc(JPEG_BUFFER_SIZE) : malloc(JPEG_BUFFER_SIZE));
  if (psramFound()) {
    frameBuffer = static_cast<uint16_t*>(ps_malloc(TFT_W * TFT_H * sizeof(uint16_t)));
    visionPreviewBuffer = static_cast<uint16_t*>(ps_malloc(VISION_LOCAL_PREVIEW_W * VISION_LOCAL_PREVIEW_H * sizeof(uint16_t)));
    visionPreviewDecodeBuffer = static_cast<uint16_t*>(ps_malloc(VISION_LOCAL_PREVIEW_W * VISION_LOCAL_PREVIEW_H * sizeof(uint16_t)));
    crossfadeOldBuf = static_cast<uint16_t*>(ps_malloc(TFT_W * TFT_H * sizeof(uint16_t)));
    crossfadeNewBuf = static_cast<uint16_t*>(ps_malloc(TFT_W * TFT_H * sizeof(uint16_t)));
    animDecodeBuffer = static_cast<uint16_t*>(ps_malloc(TFT_W * TFT_H * sizeof(uint16_t)));
    mouthFrameBuffer = static_cast<uint16_t*>(ps_malloc(TFT_W * TFT_H * sizeof(uint16_t)));
    mouthMaskBuffer = static_cast<uint16_t*>(ps_malloc(TFT_W * TFT_H * sizeof(uint16_t)));
    mouthMaskAlpha = static_cast<uint8_t*>(ps_malloc(TFT_W * TFT_H));
  } else {
    visionPreviewBuffer = static_cast<uint16_t*>(malloc(VISION_LOCAL_PREVIEW_W * VISION_LOCAL_PREVIEW_H * sizeof(uint16_t)));
    visionPreviewDecodeBuffer = static_cast<uint16_t*>(malloc(VISION_LOCAL_PREVIEW_W * VISION_LOCAL_PREVIEW_H * sizeof(uint16_t)));
    animDecodeBuffer = static_cast<uint16_t*>(malloc(TFT_W * TFT_H * sizeof(uint16_t)));
    mouthFrameBuffer = static_cast<uint16_t*>(malloc(TFT_W * TFT_H * sizeof(uint16_t)));
    mouthMaskBuffer = static_cast<uint16_t*>(malloc(TFT_W * TFT_H * sizeof(uint16_t)));
    mouthMaskAlpha = static_cast<uint8_t*>(malloc(TFT_W * TFT_H));
  }
  initAnimScaleMaps();
  jpegMutex = xSemaphoreCreateMutex();
  qFrames = xQueueCreate(3, sizeof(fb_ptr_t));
  qAudio = xQueueCreate(10, sizeof(AudioChunk));
  qCatPoses = xQueueCreate(CATPOSE_QUEUE_SIZE, sizeof(CatPoseFrame));

  for (int i = 0; i < SERVO_COUNT; ++i) {
    servos[i].setPeriodHertz(50);
    servos[i].attach(SERVO_PINS[i], SERVO_MIN_US, SERVO_MAX_US);
  }
  centerCatPose();

  initScreen();
  if (LittleFS.begin(true)) {
    LOGLN("[FS] LittleFS ready");
    scanAnimationFolders();
    scanMouthAssets();
  } else {
    LOGLN("[FS] LittleFS init failed");
  }
  initI2SIn();
  initI2SOut();
  initCamera();
  setupWebSocketCallbacks();
  setScreenPageMode(SCREEN_PAGE_EXPRESSION);
  noteInteraction();
  connectWiFi();

  // 任务 / 核心分配：
  //   core 0：cam_cap/cam_send/mic_cap/mic_upl + WiFi/TCP stack —— 吞吐密集
  //   core 1：tts_play（高优先级，独占核心喂 I2S DMA） + cat_pose（低优先级动作）
  // 这样 pose 再怎么动、WiFi/camera 再怎么忙，都不会抢占 I2S 填 buffer 的 CPU 时间。
  xTaskCreatePinnedToCore(taskCamCapture, "cam_cap", 8192, nullptr, 3, nullptr, 0);
  xTaskCreatePinnedToCore(taskCamSend, "cam_send", 6144, nullptr, 3, nullptr, 0);
  xTaskCreatePinnedToCore(taskMicCapture, "mic_cap", 4096, nullptr, 2, nullptr, 0);
  xTaskCreatePinnedToCore(taskMicUpload, "mic_upl", 4096, nullptr, 2, nullptr, 0);
  xTaskCreatePinnedToCore(taskHttpPlay, "tts_play", 8192, nullptr, 6, nullptr, 1);
  xTaskCreatePinnedToCore(taskCatPoseDriver, "cat_pose", 4096, nullptr, 1, nullptr, 1);

  LOGLN("[BOOT] cat robot ready");
}

void loop() {
  maintainWiFi();
  maintainSockets();
  serviceDirectPoseControl();
  scheduleIdleActionIfNeeded();
  refreshExpressionScreenIfNeeded();
  delay(5);
}
