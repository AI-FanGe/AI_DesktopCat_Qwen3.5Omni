// ===== 集成项目：XIAO ESP32S3 Sense =====
// 功能：屏幕显示 + 摄像头 + 麦克风 + 扬声器 + STS3032舵机 + PCA9685舵机 + WiFi + WebSocket + 步态控制
// 
// 硬件接线：
// - 屏幕 ST7789: D0(CS), D1(DC), RST接EN引脚, D8(SCK), D10(MOSI), 170x320
// - STS3032舵机: D6(TX=43), D7(RX=44), 波特率1000000, ID1=左前腿, ID2=右前腿, ID3=左后腿, ID4=右后腿
// - PCA9685: D4(SDA), D5(SCL), CH12=嘴巴, CH13=尾巴, CH14=左耳, CH15=右耳
// - MAX98357A扬声器: D3(BCLK=GPIO4), D2(LRC=GPIO3), D9(DIN=GPIO8)
// - 摄像头：ESP32S3 Sense 内置 OV2640
// - 麦克风：ESP32S3 Sense 内置 PDM (CLK=GPIO42, DATA=GPIO41)
// 
// 注意：扬声器和麦克风引脚已分开，可以同时工作！
//
// 网络功能：
// - WiFi 连接
// - WebSocket 视频传输 (/ws/camera)
// - WebSocket 音频传输 (/ws_audio)
// - HTTP 舵机控制 (/servo, /sts)
//
// 步态控制键盘命令（通过 GAIT:xxx）：
// - GAIT:WALK - 慢走 (W键)
// - GAIT:TROT - 快走 (Shift+W)
// - GAIT:TROT_STRAIGHT / GAIT:TS - 快走直线
// - GAIT:RUN  - 跑步
// - GAIT:BACKWARD / GAIT:BACK - 后退
// - GAIT:EFFICIENT_WALK / GAIT:EFF - 效率走（交替对角，无反向力）
// - GAIT:WAVE - 四腿往复
// - GAIT:IDLE - 待机 (空格)
// - GAIT:SIT  - 坐下 (S键)
// - GAIT:LAYDOWN / GAIT:LAY - 倒下
// - GAIT:NEWYEAR / GAIT:NY - 拜年
// - GAIT:STUMBLE / GAIT:FALL - 马失前蹄
// - GAIT:JUMP - 跳跃 (J键)
// - GAIT:STOP - 停止 (ESC键)
// - GAIT:LEFT/RIGHT - 转向 (A/D键)

#include <WiFi.h>
#include <esp_wifi.h>
#include <esp_system.h>
#include <esp_camera.h>
#include <ArduinoWebsockets.h>
#include "ESP_I2S.h"
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include <cstring>
#include <WiFiClient.h>
#include <math.h>

// 屏幕
#include <SPI.h>
#include <Adafruit_GFX.h>
#include <Adafruit_ST7789.h>

// 舵机
#include <Wire.h>
#include <Adafruit_PWMServoDriver.h>
#include <WebServer.h>
#include <SCServo.h>

// 文件系统（用于动画）
#include <LittleFS.h>
#include <JPEGDEC.h>

using namespace websockets;

// ====================================================================
// WiFi / Server 配置
// ====================================================================
const char* WIFI_SSID   = "YOUR_WIFI_SSID";
const char* WIFI_PASS   = "YOUR_WIFI_PASSWORD";
const char* SERVER_HOST = "192.168.2.7";
const uint16_t SERVER_PORT = 8081;

static const char* CAM_WS_PATH = "/ws/camera";
static const char* AUD_WS_PATH = "/ws_audio";

// ====================================================================
// 屏幕引脚定义（按 test_all_modules）
// RST 接到 EN 引脚，代码设为 -1
// ====================================================================
#define TFT_CS    D0
#define TFT_DC    D1
#define TFT_RST   -1    // RST 接到 EN 引脚，不使用GPIO控制
#define TFT_MOSI  D10
#define TFT_SCK   D8
#define TFT_W     170
#define TFT_H     320

Adafruit_ST7789 tft = Adafruit_ST7789(TFT_CS, TFT_DC, TFT_RST);

// ====================================================================
// PCA9685 舵机（按 test_all_modules）
// ====================================================================
#define I2C_SDA   D4
#define I2C_SCL   D5

Adafruit_PWMServoDriver pca = Adafruit_PWMServoDriver();
#define SERVO_FREQ 50
const int SERVO_MIN = 150;
const int SERVO_MAX = 600;

// ====================================================================
// STS3032 舵机（按 test_all_modules）
// ====================================================================
#define STS_TX    43   // D6
#define STS_RX    44   // D7
#define STS_BAUD  1000000

SMS_STS sts;

// ====================================================================
// MAX98357A 扬声器（重新分配引脚，避免与麦克风冲突）
// 原来用 42/41 与麦克风冲突，现改用 D2/D3
// ====================================================================
#define I2S_SPK_BCLK  4    // D3 (GPIO4)
#define I2S_SPK_LRCK  3    // D2 (GPIO3)
#define I2S_SPK_DIN   8    // D9 (保持不变)

const int TTS_RATE = 16000;

// ====================================================================
// PDM 麦克风（ESP32S3 Sense 内置）
// 注意：CLK=42, DATA=41 与扬声器引脚冲突，需要在说话时禁用麦克风
// ====================================================================
#define I2S_MIC_CLOCK_PIN 42
#define I2S_MIC_DATA_PIN  41

const int SAMPLE_RATE     = 16000;
const int CHUNK_MS        = 20;
const int BYTES_PER_CHUNK = SAMPLE_RATE * CHUNK_MS / 1000 * 2;
const int AUDIO_QUEUE_DEPTH = 10;

// ====================================================================
// 摄像头配置（ESP32S3 Sense）
// ====================================================================
#define CAMERA_MODEL_XIAO_ESP32S3
#include "camera_pins.h"

framesize_t g_frame_size = FRAMESIZE_VGA;
#define JPEG_QUALITY  17
#define FB_COUNT      2
volatile int g_target_fps = 0;

// 视频统计
volatile unsigned long frame_captured_count = 0;
volatile unsigned long frame_sent_count = 0;
volatile unsigned long frame_dropped_count = 0;

// ====================================================================
// HTTP Server
// ====================================================================
WebServer server(80);

// ====================================================================
// WebSocket 客户端
// ====================================================================
WebsocketsClient wsCam;
WebsocketsClient wsAud;
volatile bool cam_ws_ready = false;
volatile bool aud_ws_ready = false;

// ====================================================================
// 音频队列
// ====================================================================
typedef camera_fb_t* fb_ptr_t;
QueueHandle_t qFrames;

typedef struct {
  size_t n;
  uint8_t data[BYTES_PER_CHUNK];
} AudioChunk;
QueueHandle_t qAudio;

I2SClass i2sIn;   // PDM RX (Mic)
I2SClass i2sOut;  // STD TX (Speaker)
volatile bool run_audio_stream = false;
volatile bool mic_enabled = true;  // 麦克风启用标志

// ====================================================================
// HTTP TTS 播放任务
// ====================================================================
static TaskHandle_t taskHttpPlayHandle = nullptr;
static volatile bool http_play_running = false;
static volatile bool tts_playing = false;
static volatile bool tts_audio_available = false;  // 是否有音频需要播放

// ====================================================================
// 嘴巴舵机跟随音频控制（CH12）
// ====================================================================
const uint8_t MOUTH_SERVO_CH = 12;        // 嘴巴舵机通道
const int MOUTH_CLOSED_ANGLE = 87;        // 嘴巴闭合角度
const int MOUTH_OPEN_ANGLE = 124;         // 嘴巴最大张开角度
const float MOUTH_LEVEL_GAMMA = 0.55f;    // 音量gamma校正（降低gamma让中低音量也能张大嘴）
const float MOUTH_LEVEL_GAIN = 1.45f;     // 音量增益（提高增益让嘴巴张得更大）
const float MOUTH_MIN_LEVEL = 0.10f;      // 最小音量阈值

TaskHandle_t mouthTaskHandle = nullptr;
volatile bool mouthActive = false;        // 嘴巴是否激活
volatile float mouthLevelTarget = 0.0f;   // 目标音量级别（0.0-1.2）
volatile uint32_t mouthLevelTimestamp = 0; // 最后更新音量时间

// ====================================================================
// 动画播放状态
// ====================================================================
JPEGDEC jpeg;
int currentAnim = 0;
int currentFrame = 1;
bool animLoop = false;
unsigned long lastFrameTime = 0;
const int FRAME_DELAY = 50;  // 0.7倍速播放（原15ms）

#define MAX_ANIMS 20  // 最大支持20个动画
int ANIM_FRAMES[MAX_ANIMS + 1];  // 索引0不使用，1-20用于anim1-20
char ANIM_PATHS[MAX_ANIMS + 1][16];  // 存储路径，如 "/anim1", "/anim2"...
int maxAnimIndex = 0;  // 实际找到的最大动画编号

uint8_t *jpegBuffer = NULL;
const size_t JPEG_BUFFER_SIZE = 65536;
uint16_t *frameBuffer = NULL;
const size_t FRAME_BUFFER_SIZE = TFT_W * TFT_H * 2;

// 渐变过渡用的缓冲区
uint16_t *crossfadeOldBuf = NULL;   // 旧帧快照
uint16_t *crossfadeNewBuf = NULL;   // 新帧快照
bool crossfadeActive = false;       // 是否正在渐变过渡
uint32_t crossfadeStartMs = 0;
const uint32_t CROSSFADE_DURATION_MS = 500; // 0.5s 渐变

// ====================================================================
// 本地音效播放（IMA ADPCM 解码）
// ====================================================================
#define MAX_SOUNDS 20
int maxSoundIndex = 0;
char SOUND_PATHS[MAX_SOUNDS + 1][24];
uint32_t SOUND_NUM_SAMPLES[MAX_SOUNDS + 1];
uint32_t SOUND_SAMPLE_RATES[MAX_SOUNDS + 1];

volatile bool local_sound_playing = false;
volatile bool local_sound_stop_requested = false;
volatile int  local_sound_id = 0;
volatile int  animsnd_frame_delay = 0;  // ANIMSND 模式下的自适应帧延迟（0=使用默认）

TaskHandle_t taskLocalSoundHandle = nullptr;

// ADPCM 文件头
struct AdpFileHeader {
  char     magic[4];      // "ADPM"
  uint32_t sampleRate;
  uint32_t numSamples;
  uint32_t dataSize;
};

// IMA ADPCM 解码表
static const int16_t ima_step_table[89] = {
    7,8,9,10,11,12,13,14,16,17,19,21,23,25,28,31,34,37,41,45,
    50,55,60,66,73,80,88,97,107,118,130,143,157,173,190,209,230,253,279,307,
    337,371,408,449,494,544,598,658,724,796,876,963,1060,1166,1282,1411,1552,
    1707,1878,2066,2272,2499,2749,3024,3327,3660,4026,4428,4871,5358,5894,6484,
    7132,7845,8630,9493,10442,11487,12635,13899,15289,16818,18500,20350,22385,
    24623,27086,29794,32767
};
static const int8_t ima_index_table[16] = {
    -1,-1,-1,-1, 2,4,6,8, -1,-1,-1,-1, 2,4,6,8
};

static inline int16_t adpcm_decode_sample(uint8_t nibble, int16_t &pred, int8_t &idx) {
  int step = ima_step_table[idx];
  int diff = step >> 3;
  if (nibble & 1) diff += step >> 2;
  if (nibble & 2) diff += step >> 1;
  if (nibble & 4) diff += step;
  if (nibble & 8) diff = -diff;
  int32_t p = pred + diff;
  if (p > 32767) p = 32767;
  if (p < -32768) p = -32768;
  pred = (int16_t)p;
  int ni = idx + ima_index_table[nibble & 0x0F];
  if (ni < 0) ni = 0;
  if (ni > 88) ni = 88;
  idx = (int8_t)ni;
  return pred;
}

// 音效播放缓冲区（静态分配避免栈溢出）
static uint8_t  sndAdpcmBuf[256];
static int32_t  sndOutBuf[1024];  // 512采样 * 2声道

// ====================================================================
// 系统状态
// ====================================================================
enum SystemMode {
  MODE_OFFLINE = 0,    // 离线模式（仅本地功能）
  MODE_CONNECTING,     // 正在连接
  MODE_ONLINE,         // 在线模式（全功能）
  MODE_ANIMATION       // 动画播放模式
};
volatile SystemMode systemMode = MODE_OFFLINE;

// ====================================================================
// 步态控制状态
// ====================================================================
enum GaitMode {
  GAIT_STOP = 0,       // 停止
  GAIT_WALK,           // 慢走
  GAIT_TROT,           // 快走（对角步态）
  GAIT_RUN,            // 跑步
  GAIT_IDLE,           // 待机（站立微晃）
  GAIT_SIT,            // 坐下
  GAIT_JUMP,           // 跳跃
  GAIT_TROT_STRAIGHT,  // 快走直线
  GAIT_BACKWARD,       // 后退
  GAIT_EFFICIENT_WALK, // 效率走（交替对角，无反向力）
  GAIT_WAVE,           // 四腿往复
  GAIT_LAYDOWN,        // 倒下
  GAIT_NEWYEAR,        // 拜年
  GAIT_STUMBLE         // 马失前蹄
};
volatile GaitMode currentGait = GAIT_STOP;
volatile GaitMode targetGait = GAIT_STOP;
volatile float turnFactor = 0.0f;        // -1.0 左转, 0 直走, 1.0 右转
volatile float turnStrength = 0.4f;      // 转向强度

// 步态任务句柄
static TaskHandle_t gaitTaskHandle = nullptr;
volatile bool gaitRunning = false;

// ============ 慢走(Walk)步态配置 ============
// 总周期2.5秒，四点运动轨迹
struct LegConfig {
  int ready;      // 准备位置（后方靠前）
  int back;       // 最后方位置
  int front;      // 最前方位置
  int neutral;    // 中立位
  int phase;      // 相位（0-3）
};

// 舵机ID配置: 1=左前腿, 2=右前腿, 3=左后腿, 4=右后腿
const LegConfig WALK_CONFIG[] = {
  {2093, 1888, 2408, 2198, 1},  // ID1: 左前腿
  {2003, 2208, 1688, 1898, 3},  // ID2: 右前腿
  {1898, 1748, 2348, 2048, 0},  // ID3: 左后腿
  {2198, 2348, 1748, 2048, 2},  // ID4: 右后腿
};

// 慢走周期参数（单位：秒）
const float WALK_CYCLE_TOTAL = 2.5f;
const float WALK_PHASE_1 = 0.25f;  // ready→back
const float WALK_PHASE_2 = 0.85f;  // back→front
const float WALK_PHASE_3 = 0.40f;  // front→neutral
const float WALK_PHASE_4 = 1.00f;  // neutral→ready
const float WALK_PHASE_DELAY = 0.625f;  // 每腿相位差
const int WALK_ACCEL_FAST = 250;   // 前半程：快速推出
const int WALK_ACCEL_SLOW = 30;    // 后半程：缓缓收回
const int WALK_ACCEL_TRANS = 150;  // 过渡阶段：平滑速度变化

// ============ 快走(Trot)步态配置 ============
// 总周期0.9秒，对角步态
const LegConfig TROT_CONFIG[] = {
  {2041, 1733, 2513, 2198, 0},  // ID1: 左前腿（与右后腿同步）
  {2056, 2363, 1583, 1898, 1},  // ID2: 右前腿（与左后腿同步）
  {1823, 1448, 2498, 2048, 1},  // ID3: 左后腿
  {2273, 2648, 1598, 2048, 0},  // ID4: 右后腿
};

const float TROT_CYCLE_TOTAL = 0.9f;
const float TROT_PHASE_1 = 0.10f;  // ready→back 蓄力
const float TROT_PHASE_2 = 0.35f;  // back→front 发力推出
const float TROT_PHASE_3 = 0.15f;  // front→neutral 回到中立
const float TROT_PHASE_4 = 0.30f;  // neutral→ready 快速收回
const float TROT_PHASE_DELAY = 0.45f;  // 对角相位差（半周期）
const int TROT_ACCEL_FAST = 254;   // 前半程：快速推出
const int TROT_ACCEL_SLOW = 100;   // 后半程：快速收回
const int TROT_ACCEL_TRANS = 200;  // 过渡阶段：平滑速度变化

// ============ 跑步(Run)步态配置 ============
// 总周期0.6秒，顺序步态（左后→右后→左前→右前）
const LegConfig RUN_CONFIG[] = {
  {2041, 1733, 2513, 2198, 2},  // ID1: 左前腿
  {2056, 2363, 1583, 1898, 3},  // ID2: 右前腿
  {1823, 1448, 2498, 2048, 0},  // ID3: 左后腿
  {2273, 2648, 1598, 2048, 1},  // ID4: 右后腿
};

const float RUN_CYCLE_TOTAL = 0.6f;
const float RUN_PHASE_1 = 0.07f;
const float RUN_PHASE_2 = 0.23f;
const float RUN_PHASE_3 = 0.10f;
const float RUN_PHASE_4 = 0.20f;
const float RUN_PHASE_DELAY = 0.15f;
const int RUN_ACCEL_FAST = 254;
const int RUN_ACCEL_SLOW = 254;

// ============ 待机(Idle)步态配置 ============
struct IdleConfig {
  int base;         // 基础位置
  int swayRange;    // 微晃范围
  int kickRange;    // 踢腿范围
};

const IdleConfig IDLE_CONFIG[] = {
  {2048, 35, 350},  // ID1: 左前腿
  {2048, 38, 320},  // ID2: 右前腿
  {2048, 40, 300},  // ID3: 左后腿
  {2048, 45, 280},  // ID4: 右后腿
};

// ============ 坐下配置 ============
const int SIT_BACK_ANGLE = 778;  // 后腿向前70度

// ============ 跳跃配置 ============
const int JUMP_CONTRACTION = 444;  // 收缩40度
const int JUMP_OVERSHOOT = 222;    // 弹出过冲20度

// ============ 快走直线(TrotStraight) ============
// 与倒退共用 BACKWARD_CONFIG，仅相位顺序相反：直线走 = ready→front→back→neutral→ready（倒退 = ready→back→front→...）
// 这样保证腿部 pattern 完全一致，只是方向相反（镜像）。

// ============ 后退(Backward)步态配置 ============
// 基于快走(Trot)配置，交换front/back位置实现后退
// 前腿(ID1+ID2)同步phase=0，后腿(ID3+ID4)同步phase=1
const LegConfig BACKWARD_CONFIG[] = {
  {2041, 2593, 1653, 2198, 0},  // ID1: 左前腿 (与右后腿对角同步) - 增大行程80
  {2056, 1503, 2443, 1898, 1},  // ID2: 右前腿 (与左后腿对角同步) - 增大行程80
  {1823, 2498, 1448, 2048, 1},  // ID3: 左后腿 (与右前腿对角同步)
  {2273, 1598, 2648, 2048, 0},  // ID4: 右后腿 (与左前腿对角同步)
};

const float BACKWARD_CYCLE_TOTAL = 1.45f;
const float BACKWARD_PHASE_1 = 0.14f;   // ready→back 蓄力
const float BACKWARD_PHASE_2 = 0.54f;   // back→front 推出
const float BACKWARD_PHASE_3 = 0.54f;   // front→neutral 回中
const float BACKWARD_PHASE_4 = 0.23f;   // neutral→ready 收回
const float BACKWARD_PHASE_DELAY = 0.725f;  // 半周期相位差，实现跳跑对角步态
const int BACKWARD_ACCEL_FAST = 254;
const int BACKWARD_ACCEL_SLOW = 120;
const int BACKWARD_ACCEL_TRANS = 200;

// ============ 效率走(Efficient Walk) ============
// 交替对角：同一时刻只有一对对角腿在做推进，另一对保持中立不发力，避免前后力抵消
// 用 BACKWARD_CONFIG 位置；每对角 1s 一周期，总周期 2s
const float EFF_WALK_HALF_CYCLE = 1.0f;   // 每对角执行周期(秒)
const float EFF_WALK_PHASE_1 = 0.10f;     // ready→front
const float EFF_WALK_PHASE_2 = 0.37f;     // front→back 推进
const float EFF_WALK_PHASE_3 = 0.37f;     // back→neutral
const float EFF_WALK_PHASE_4 = 0.16f;     // neutral→ready

// ============ 四腿往复(Wave)步态配置 ============
// 四条腿同时来回运动，流畅的正弦波律动
const float WAVE_CYCLE_TOTAL = 2.0f;  // 总周期加长，更流畅
const int WAVE_AMPLITUDE = 500;        // 运动幅度
const int WAVE_SPEED = 800;            // 运动速度
const int WAVE_ACCEL = 180;            // 加速度，降低使运动更柔和

// ============ 拜年(NewYear)步态配置 ============
// 前腿在上和中立位之间缓慢运动，后腿像坐下姿势，耳朵偶尔动
const float NEWYEAR_CYCLE_TOTAL = 2.5f;  // 总周期2.5秒
// 左前腿(ID1)：数值增大=向上
const int NEWYEAR_LEFT_HIGH = 2600;      // 左前腿抬起位置
const int NEWYEAR_LEFT_MID = 2198;       // 左前腿中立位置
// 右前腿(ID2)：数值减小=向上
const int NEWYEAR_RIGHT_HIGH = 1400;     // 右前腿抬起位置（低值）
const int NEWYEAR_RIGHT_MID = 1898;      // 右前腿中立位置
// 后腿姿势（坐下）
const int NEWYEAR_BACK_LEFT = 2826;      // 左后腿坐姿 (2048+778)
const int NEWYEAR_BACK_RIGHT = 1270;     // 右后腿坐姿 (2048-778)
const int NEWYEAR_SPEED = 600;           // 缓慢运动速度
const int NEWYEAR_ACCEL = 100;           // 柔和加速度

// ============ 倒下(Laydown)配置 ============
const int LAYDOWN_FRONT_ANGLE = 778;  // 前腿向前倒
const int LAYDOWN_BACK_ANGLE = 778;   // 后腿向后倒

// ============ 情感动作系统 ============
// 从服务器接收情绪驱动的动作帧，在 IDLE 模式下执行
struct EmoteFrame {
  int legPos[4];     // 四条腿目标位置 (ID1-4)
  int legSpeed;      // 腿部运动速度
  int legAccel;      // 腿部加速度
  int tailAngle;     // 尾巴角度 (0-180)
  int earLAngle;     // 左耳角度 (0-180)
  int earRAngle;     // 右耳角度 (0-180)
  int durationMs;    // 持续时间(ms)
};

#define EMOTE_QUEUE_SIZE 12
QueueHandle_t qEmoteFrames = NULL;

// 状态显示
String statusLine1 = "System Starting...";
String statusLine2 = "";
String statusLine3 = "";

enum ScreenPageMode {
  SCREEN_PAGE_EXPRESSION = 0,
  SCREEN_PAGE_OPENCLAW = 1,
  SCREEN_PAGE_HOST_CAMERA = 2
};

volatile ScreenPageMode screenPageMode = SCREEN_PAGE_EXPRESSION;
uint16_t *remoteScreenBuffer = NULL;
volatile bool remoteScreenDirty = false;
volatile uint32_t remoteScreenLastMs = 0;

// ====================================================================
// 前向声明
// ====================================================================
void setServoAngle(uint8_t ch, int angleDeg);
void displayStatus();
void handleRoot();
void handleServo();
void handleSTS();
void handleStatus();
void handleGait();
void taskMouthDriver(void* pvParams);
void feedMouthLevelFromSamples(const int16_t* samples, size_t count);
void requestMouthStart();
void requestMouthIdle();
void setScreenPageMode(ScreenPageMode mode);
void updateRemoteScreenFrame(const uint8_t *data, size_t len);
void renderRemoteScreenIfNeeded();

// 步态控制函数
void startGait(GaitMode mode);
void stopGait();
void taskGaitControl(void* pvParams);
void runWalkCycle();
void runTrotCycle();
void runRunCycle();
void runIdleLoop();
void runSitAction();
void runJumpAction();
void runTrotStraightCycle();
void runBackwardCycle();
void runEfficiencyWalkCycle();
void runWaveCycle();
void runLaydownAction();
void runNewYearAction();
void runStumbleAction();
void resetLegsToCenter();
int calcSpeed(int displacement, float timeSec);

// 辅助函数：发送STS舵机命令
inline void stsMove(int id, int pos, int speed, int accel) {
  pos = constrain(pos, 0, 4095);
  speed = constrain(speed, 0, 5000);
  accel = constrain(accel, 0, 254);
  sts.WritePosEx(id, pos, speed, accel);
}

// ====================================================================
// 舵机控制
// ====================================================================
void setServoAngle(uint8_t ch, int angleDeg) {
  angleDeg = constrain(angleDeg, 0, 180);
  int pulselen = map(angleDeg, 0, 180, SERVO_MIN, SERVO_MAX);
  pca.setPWM(ch, 0, pulselen);
}

// ====================================================================
// 始终按几率逻辑切到随机动画循环，绝不显示 OFFLINE 画面
// 与 playAnimationFrame 内概率一致：待机(1/2/3)60% 开心+害羞(6/8)30% 哭+生气+难过(4/5/7)10%
// ====================================================================
void switchToIdleOrOffline() {
  animLoop = true;
  int r = random(100);
  if (r < 60) {
    int idleAnims[] = {1, 2, 3};
    currentAnim = idleAnims[random(3)];
  } else if (r < 90) {
    currentAnim = random(2) == 0 ? 6 : 8;
  } else {
    int emoAnims[] = {4, 5, 7};
    currentAnim = emoAnims[random(3)];
  }
  if (currentAnim > maxAnimIndex || ANIM_FRAMES[currentAnim] == 0) {
    currentAnim = 1;
  }
  currentFrame = 1;
  lastFrameTime = 0;
  systemMode = MODE_ANIMATION;
  Serial.printf("[ANIM] Random loop -> anim%d\n", currentAnim);
}

// ====================================================================
// 远程屏幕页面
// 0=本地表情动画，1=OpenClaw 面板，2=电脑摄像头
// ====================================================================
void drawRemoteScreenWaiting(const char *title, const char *subtitle) {
  tft.fillScreen(ST77XX_WHITE);
  tft.drawRoundRect(8, 18, 154, 284, 14, tft.color565(220, 220, 225));
  tft.fillRoundRect(16, 28, 138, 92, 18, tft.color565(27, 22, 34));
  tft.fillCircle(42, 63, 20, tft.color565(225, 45, 63));
  tft.fillCircle(42, 40, 11, tft.color565(255, 170, 178));
  tft.fillRoundRect(28, 79, 28, 26, 8, tft.color565(132, 24, 40));
  tft.setTextWrap(false);
  tft.setTextColor(tft.color565(248, 248, 250));
  tft.setTextSize(2);
  tft.setCursor(72, 44);
  tft.print(title);
  tft.setTextSize(1);
  tft.setTextColor(tft.color565(255, 196, 202));
  tft.setCursor(72, 72);
  tft.print(subtitle);
  tft.setTextColor(tft.color565(88, 88, 94));
  tft.setCursor(22, 156);
  tft.print("Waiting for server screen...");
  tft.setCursor(22, 176);
  tft.print("Cover pony camera to switch.");
}

void setScreenPageMode(ScreenPageMode mode) {
  screenPageMode = mode;
  remoteScreenDirty = false;
  remoteScreenLastMs = millis();
  Serial.printf("[SCREEN] page mode -> %d\n", (int)mode);
  if (mode == SCREEN_PAGE_EXPRESSION) {
    return;
  }
  if (mode == SCREEN_PAGE_OPENCLAW) {
    drawRemoteScreenWaiting("OPEN", "OpenClaw dashboard");
  } else if (mode == SCREEN_PAGE_HOST_CAMERA) {
    drawRemoteScreenWaiting("CAM", "Host camera stream");
  }
}

void updateRemoteScreenFrame(const uint8_t *data, size_t len) {
  if (data == NULL || len == 0) return;
  if (len > JPEG_BUFFER_SIZE) {
    Serial.printf("[SCREEN] jpeg too large len=%u\n", (unsigned)len);
    return;
  }
  if (jpeg.openRAM((uint8_t *)data, len, jpegDrawCallback)) {
    jpeg.setPixelType(RGB565_LITTLE_ENDIAN);
    jpeg.decode(0, 0, 0);
    jpeg.close();
    if (frameBuffer != NULL) {
      tft.drawRGBBitmap(0, 0, frameBuffer, TFT_W, TFT_H);
    }
  } else {
    Serial.printf("[SCREEN] jpeg decode failed len=%u\n", (unsigned)len);
    return;
  }
  remoteScreenDirty = false;
  remoteScreenLastMs = millis();
}

void renderRemoteScreenIfNeeded() {
  if (screenPageMode == SCREEN_PAGE_EXPRESSION) return;
}

// ====================================================================
// 屏幕状态显示
// ====================================================================
void displayStatus() {
  tft.fillScreen(ST77XX_BLACK);
  tft.setCursor(5, 10);
  tft.setTextColor(ST77XX_WHITE);
  tft.setTextSize(2);
  
  // 模式指示
  tft.setTextColor(ST77XX_YELLOW);
  switch(systemMode) {
    case MODE_OFFLINE:
      tft.println("OFFLINE");
      break;
    case MODE_CONNECTING:
      tft.println("CONNECTING...");
      break;
    case MODE_ONLINE:
      tft.println("ONLINE");
      break;
    case MODE_ANIMATION:
      tft.println("ANIMATION");
      break;
  }
  
  tft.setTextColor(ST77XX_WHITE);
  tft.setCursor(5, 40);
  tft.setTextSize(1);
  tft.println(statusLine1);
  tft.println(statusLine2);
  tft.println(statusLine3);
  
  // WiFi 信息
  if (WiFi.status() == WL_CONNECTED) {
    tft.setTextColor(ST77XX_GREEN);
    tft.setCursor(5, 100);
    tft.print("IP: ");
    tft.println(WiFi.localIP());
  }
}

// ====================================================================
// JPEG 解码回调（用于动画）
// ====================================================================
int jpegDrawCallback(JPEGDRAW *pDraw) {
  if (frameBuffer == NULL) {
    tft.drawRGBBitmap(pDraw->x, pDraw->y, pDraw->pPixels, pDraw->iWidth, pDraw->iHeight);
  } else {
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
  return 1;
}

// RGB565 像素混合：alpha 范围 0~256（256=完全取 newPx）
static inline uint16_t blendRGB565(uint16_t oldPx, uint16_t newPx, uint16_t alpha) {
  uint16_t invA = 256 - alpha;
  uint16_t r = ((((oldPx >> 11) & 0x1F) * invA + ((newPx >> 11) & 0x1F) * alpha) >> 8);
  uint16_t g = ((((oldPx >> 5) & 0x3F) * invA + ((newPx >> 5) & 0x3F) * alpha) >> 8);
  uint16_t b = ((((oldPx) & 0x1F) * invA + ((newPx) & 0x1F) * alpha) >> 8);
  return (r << 11) | (g << 5) | b;
}

// 把当前 frameBuffer 存为旧帧快照，解码 targetAnim 第 1 帧到新帧快照，启动渐变
void startCrossfade(int targetAnim) {
  if (!frameBuffer || !crossfadeOldBuf || !crossfadeNewBuf) return;
  if (targetAnim < 1 || targetAnim > maxAnimIndex || ANIM_FRAMES[targetAnim] == 0) return;

  memcpy(crossfadeOldBuf, frameBuffer, TFT_W * TFT_H * sizeof(uint16_t));

  char fn[32];
  sprintf(fn, "%s/%04d.jpg", ANIM_PATHS[targetAnim], 1);
  File f = LittleFS.open(fn, "r");
  if (!f) return;
  size_t sz = f.size();
  if (sz > JPEG_BUFFER_SIZE) { f.close(); return; }
  size_t rd = f.read(jpegBuffer, sz);
  f.close();
  if (rd != sz) return;

  if (jpeg.openRAM(jpegBuffer, sz, jpegDrawCallback)) {
    jpeg.setPixelType(RGB565_LITTLE_ENDIAN);
    jpeg.decode(0, 0, 0);
    jpeg.close();
  }
  memcpy(crossfadeNewBuf, frameBuffer, TFT_W * TFT_H * sizeof(uint16_t));

  crossfadeActive = true;
  crossfadeStartMs = millis();
  Serial.printf("[ANIM] Crossfade start -> anim%d\n", targetAnim);
}

// 每帧调用：若渐变激活，混合输出到屏幕，返回 true 表示正在渐变
bool updateCrossfade() {
  if (!crossfadeActive) return false;
  uint32_t elapsed = millis() - crossfadeStartMs;
  if (elapsed >= CROSSFADE_DURATION_MS) {
    crossfadeActive = false;
    tft.drawRGBBitmap(0, 0, crossfadeNewBuf, TFT_W, TFT_H);
    return false;
  }
  uint16_t alpha = (uint16_t)((elapsed * 256) / CROSSFADE_DURATION_MS);
  int total = TFT_W * TFT_H;
  for (int i = 0; i < total; i++) {
    frameBuffer[i] = blendRGB565(crossfadeOldBuf[i], crossfadeNewBuf[i], alpha);
  }
  tft.drawRGBBitmap(0, 0, frameBuffer, TFT_W, TFT_H);
  return true;
}

bool displayJPEG(const char* filename) {
  File file = LittleFS.open(filename, "r");
  if (!file) return false;
  
  size_t fileSize = file.size();
  if (fileSize > JPEG_BUFFER_SIZE) {
    file.close();
    return false;
  }
  
  size_t bytesRead = file.read(jpegBuffer, fileSize);
  file.close();
  
  if (bytesRead != fileSize) return false;
  
  if (jpeg.openRAM(jpegBuffer, fileSize, jpegDrawCallback)) {
    jpeg.setPixelType(RGB565_LITTLE_ENDIAN);
    jpeg.decode(0, 0, 0);
    jpeg.close();
    
    if (frameBuffer != NULL) {
      tft.drawRGBBitmap(0, 0, frameBuffer, TFT_W, TFT_H);
    }
    return true;
  }
  return false;
}

// 按概率选一个随机动画编号（和 switchToIdleOrOffline 一致）
int pickRandomAnim() {
  int r = random(100);
  int next;
  if (r < 60) {
    int idleAnims[] = {1, 2, 3};
    next = idleAnims[random(3)];
  } else if (r < 90) {
    next = random(2) == 0 ? 6 : 8;
  } else {
    int emoAnims[] = {4, 5, 7};
    next = emoAnims[random(3)];
  }
  if (next > maxAnimIndex || ANIM_FRAMES[next] == 0) next = 1;
  return next;
}

void playAnimationFrame() {
  if (currentAnim < 1 || currentAnim > maxAnimIndex) return;
  
  char filename[32];
  sprintf(filename, "%s/%04d.jpg", ANIM_PATHS[currentAnim], currentFrame);
  
  if (displayJPEG(filename)) {
    currentFrame++;
    if (currentFrame > ANIM_FRAMES[currentAnim]) {
      if (animLoop) {
        // 循环模式：动画自然播完，直接切到下一个（无缝衔接，不渐变）
        currentAnim = pickRandomAnim();
        currentFrame = 1;
        Serial.printf("[ANIM] Random next -> anim %d\n", currentAnim);
      } else {
        // 非循环（如 EXPR 表情播完）：用渐变过渡回随机 idle
        int nextAnim = pickRandomAnim();
        animsnd_frame_delay = 0;
        animLoop = true;
        startCrossfade(nextAnim);
        currentAnim = nextAnim;
        currentFrame = 2;  // 第 1 帧已在 crossfade 中解码
        systemMode = MODE_ANIMATION;
        Serial.printf("[ANIM] EXPR done -> crossfade to anim%d\n", nextAnim);
      }
    }
  } else {
    // 解码失败：渐变到随机 idle
    int nextAnim = pickRandomAnim();
    animsnd_frame_delay = 0;
    animLoop = true;
    startCrossfade(nextAnim);
    currentAnim = nextAnim;
    currentFrame = 2;
    systemMode = MODE_ANIMATION;
  }
}

int countAnimFrames(const char* animPath) {
  int count = 0;
  char filename[32];
  for (int i = 1; i <= 9999; i++) {
    sprintf(filename, "%s/%04d.jpg", animPath, i);
    if (LittleFS.exists(filename)) {
      count = i;
    } else {
      break;
    }
  }
  return count;
}

// ====================================================================
// 本地音效播放任务
// ====================================================================
void startLocalSound(int sndId) {
  if (sndId < 1 || sndId > maxSoundIndex || SOUND_NUM_SAMPLES[sndId] == 0) return;
  if (local_sound_playing) {
    local_sound_stop_requested = true;
    int timeout = 50;
    while (local_sound_playing && timeout-- > 0) delay(10);
  }
  local_sound_id = sndId;
}

void stopLocalSound() {
  if (local_sound_playing) {
    local_sound_stop_requested = true;
  }
  local_sound_id = 0;
  animsnd_frame_delay = 0;
}

float getSoundDurationMs(int sndId) {
  if (sndId < 1 || sndId > maxSoundIndex || SOUND_SAMPLE_RATES[sndId] == 0) return 0;
  return (float)SOUND_NUM_SAMPLES[sndId] / SOUND_SAMPLE_RATES[sndId] * 1000.0f;
}

void startAnimWithSound(int animId, int sndId) {
  if (animId < 1 || animId > maxAnimIndex || ANIM_FRAMES[animId] == 0) return;

  // 计算自适应帧延迟让动画和音效同步结束
  if (sndId >= 1 && sndId <= maxSoundIndex && SOUND_NUM_SAMPLES[sndId] > 0) {
    float soundMs = getSoundDurationMs(sndId);
    int numFrames = ANIM_FRAMES[animId];
    int calcDelay = (int)(soundMs / numFrames);
    animsnd_frame_delay = constrain(calcDelay, 10, 200);
    Serial.printf("[ANIMSND] anim%d(%d frames) + s%d(%.0fms) -> %dms/frame\n",
                  animId, numFrames, sndId, soundMs, animsnd_frame_delay);
    startLocalSound(sndId);
  } else {
    animsnd_frame_delay = 0;
  }

  animLoop = false;
  currentAnim = animId;
  currentFrame = 1;
  lastFrameTime = 0;
  systemMode = MODE_ANIMATION;
}

void taskLocalSound(void*) {
  Serial.println("[SND] Task started");

  while (true) {
    if (local_sound_id == 0) {
      vTaskDelay(pdMS_TO_TICKS(50));
      continue;
    }

    int sndId = local_sound_id;
    local_sound_id = 0;

    if (sndId < 1 || sndId > maxSoundIndex) continue;

    // TTS 优先：TTS 正在播放时跳过音效
    if (tts_playing) {
      Serial.println("[SND] Skipped - TTS playing");
      continue;
    }

    File f = LittleFS.open(SOUND_PATHS[sndId], "r");
    if (!f) {
      Serial.printf("[SND] Failed to open %s\n", SOUND_PATHS[sndId]);
      continue;
    }

    AdpFileHeader hdr;
    if (f.read((uint8_t*)&hdr, sizeof(hdr)) != sizeof(hdr) ||
        memcmp(hdr.magic, "ADPM", 4) != 0) {
      f.close();
      Serial.println("[SND] Invalid file header");
      continue;
    }

    Serial.printf("[SND] Playing s%d (%u samples, %uHz, %.1fs)\n",
                  sndId, hdr.numSamples, hdr.sampleRate,
                  (float)hdr.numSamples / hdr.sampleRate);

    // 如果采样率不同于当前I2S配置，重新配置
    static uint32_t snd_current_rate = 0;
    if (snd_current_rate != hdr.sampleRate && !tts_playing) {
      i2sOut.end();
      i2sOut.setPins(I2S_SPK_BCLK, I2S_SPK_LRCK, I2S_SPK_DIN);
      if (!i2sOut.begin(I2S_MODE_STD, (int)hdr.sampleRate, I2S_DATA_BIT_WIDTH_32BIT, I2S_SLOT_MODE_STEREO)) {
        Serial.println("[SND] I2S reconfig failed");
        f.close();
        continue;
      }
      snd_current_rate = hdr.sampleRate;
    }

    local_sound_playing = true;
    local_sound_stop_requested = false;
    mic_enabled = false;

    int16_t predictor = 0;
    int8_t step_index = 0;
    uint32_t remaining = hdr.dataSize;

    while (remaining > 0 && !local_sound_stop_requested && !tts_playing) {
      uint32_t toRead = remaining < sizeof(sndAdpcmBuf) ? remaining : sizeof(sndAdpcmBuf);
      int got = f.read(sndAdpcmBuf, toRead);
      if (got <= 0) break;
      remaining -= got;

      // 每字节解码2个采样 -> 立体声32位输出
      int sampleCount = got * 2;
      for (int i = 0; i < got; i++) {
        int16_t s1 = adpcm_decode_sample(sndAdpcmBuf[i] & 0x0F, predictor, step_index);
        int16_t s2 = adpcm_decode_sample((sndAdpcmBuf[i] >> 4) & 0x0F, predictor, step_index);
        int32_t v1 = ((int32_t)(s1 * 0.7f)) << 16;
        int32_t v2 = ((int32_t)(s2 * 0.7f)) << 16;
        sndOutBuf[i * 4]     = v1;
        sndOutBuf[i * 4 + 1] = v1;
        sndOutBuf[i * 4 + 2] = v2;
        sndOutBuf[i * 4 + 3] = v2;
      }

      size_t bytes = sampleCount * 2 * sizeof(int32_t);
      size_t off = 0;
      while (off < bytes && !local_sound_stop_requested && !tts_playing) {
        size_t wrote = i2sOut.write((uint8_t*)sndOutBuf + off, bytes - off);
        if (wrote == 0) vTaskDelay(pdMS_TO_TICKS(1));
        else off += wrote;
      }
    }

    f.close();
    local_sound_playing = false;
    mic_enabled = true;
    animsnd_frame_delay = 0;
    Serial.printf("[SND] Done playing s%d\n", sndId);
  }
}

// ====================================================================
// 摄像头初始化
// ====================================================================
bool init_camera() {
  camera_config_t config;
  config.ledc_channel = LEDC_CHANNEL_0;
  config.ledc_timer   = LEDC_TIMER_0;
  config.pin_d0 = Y2_GPIO_NUM; config.pin_d1 = Y3_GPIO_NUM;
  config.pin_d2 = Y4_GPIO_NUM; config.pin_d3 = Y5_GPIO_NUM;
  config.pin_d4 = Y6_GPIO_NUM; config.pin_d5 = Y7_GPIO_NUM;
  config.pin_d6 = Y8_GPIO_NUM; config.pin_d7 = Y9_GPIO_NUM;
  config.pin_xclk = XCLK_GPIO_NUM; config.pin_pclk = PCLK_GPIO_NUM;
  config.pin_vsync = VSYNC_GPIO_NUM; config.pin_href = HREF_GPIO_NUM;
  config.pin_sscb_sda = SIOD_GPIO_NUM; config.pin_sscb_scl = SIOC_GPIO_NUM;
  config.pin_pwdn  = PWDN_GPIO_NUM; config.pin_reset = RESET_GPIO_NUM;

  config.xclk_freq_hz = 20000000;
  config.pixel_format = PIXFORMAT_JPEG;
  config.frame_size   = g_frame_size;
  config.jpeg_quality = JPEG_QUALITY;
  config.fb_count     = FB_COUNT;
  config.fb_location  = CAMERA_FB_IN_PSRAM;
  config.grab_mode    = CAMERA_GRAB_LATEST;

  esp_err_t err = esp_camera_init(&config);
  if (err != ESP_OK) {
    Serial.printf("[CAM] init failed: 0x%x\n", err);
    return false;
  }

  sensor_t * s = esp_camera_sensor_get();
  if (s) {
    // 摄像头画面旋转 180°（源头处理，而不是前端画布旋转）
    // 180° 等效于：水平镜像 + 垂直翻转
    s->set_hmirror(s, 1);
    s->set_vflip(s, 1);
    s->set_brightness(s, 0);
    s->set_contrast(s, 1);
  }
  return true;
}

// ====================================================================
// I2S 初始化
// ====================================================================
void init_i2s_in() {
  i2sIn.setPinsPdmRx(I2S_MIC_CLOCK_PIN, I2S_MIC_DATA_PIN);
  if (!i2sIn.begin(I2S_MODE_PDM_RX, SAMPLE_RATE, I2S_DATA_BIT_WIDTH_16BIT, I2S_SLOT_MODE_MONO)) {
    Serial.println("[I2S IN] init failed");
  } else {
    Serial.println("[I2S IN] PDM RX ready");
  }
}

void init_i2s_out() {
  i2sOut.setPins(I2S_SPK_BCLK, I2S_SPK_LRCK, I2S_SPK_DIN);
  if (!i2sOut.begin(I2S_MODE_STD, TTS_RATE, I2S_DATA_BIT_WIDTH_32BIT, I2S_SLOT_MODE_STEREO)) {
    Serial.println("[I2S OUT] init failed");
  } else {
    Serial.println("[I2S OUT] STD TX ready");
  }
}

// ====================================================================
// 嘴巴舵机控制函数
// ====================================================================
void feedMouthLevelFromSamples(const int16_t* samples, size_t count) {
  if (!samples || count == 0) {
    return;
  }
  
  // 计算RMS和峰值
  uint64_t sumSq = 0;
  int32_t peak = 0;
  for (size_t i = 0; i < count; ++i) {
    int32_t sample = samples[i];
    int32_t absSample = sample >= 0 ? sample : -sample;
    if (absSample > peak) peak = absSample;
    sumSq += (int64_t)sample * (int64_t)sample;
  }
  float rms = sqrtf((float)sumSq / (float)count) / 32768.0f;
  float pk  = (float)peak / 32768.0f;
  float level = rms;
  if (pk > level) level = pk;
  if (level < 0.0f) level = 0.0f;
  if (level > 1.0f) level = 1.0f;
  
  // 最小音量阈值：低于阈值时视为静音
  if (level < MOUTH_MIN_LEVEL) {
    level = 0.0f;  // 设为0，让嘴巴目标变成闭合
  }
  
  // Gamma校正和增益
  if (level > 0.0f) {
    level = powf(level, MOUTH_LEVEL_GAMMA) * MOUTH_LEVEL_GAIN;
    if (level > 1.2f) level = 1.2f;
  }
  
  // 更新目标音量级别（无论是否有声音都更新，确保静音时也能驱动闭合）
  mouthLevelTarget = level;
  mouthLevelTimestamp = millis();
}

void requestMouthStart() {
  mouthActive = true;
  mouthLevelTarget = 0.0f;
  mouthLevelTimestamp = millis();
}

void requestMouthIdle() {
  mouthActive = false;
  mouthLevelTarget = 0.0f;
  mouthLevelTimestamp = millis();
}

// 嘴巴驱动任务
void taskMouthDriver(void*) {
  Serial.println("[MOUTH] Driver task started");
  
  int lastServoAngle = MOUTH_CLOSED_ANGLE;  // 上次写入舵机的角度
  setServoAngle(MOUTH_SERVO_CH, lastServoAngle);
  
  for(;;) {
    uint32_t now = millis();
    
    if (!mouthActive) {
      mouthLevelTarget = 0.0f;
    } else {
      // 音量数据超时衰减
      int32_t since = (int32_t)(now - mouthLevelTimestamp);
      if (since > 120) {
        mouthLevelTarget *= 0.80f;
      }
      if (since > 400) {
        mouthLevelTarget = 0.0f;
      }
    }
    
    // 计算目标角度
    float level = mouthActive ? mouthLevelTarget : 0.0f;
    if (level < 0.0f) level = 0.0f;
    if (level > 1.2f) level = 1.2f;
    
    int targetAngle;
    if (level < 0.01f) {
      // 静音：目标直接就是87度（完全闭合）
      targetAngle = MOUTH_CLOSED_ANGLE;
    } else {
      // 有声音：映射到角度范围
      targetAngle = MOUTH_CLOSED_ANGLE + (int)((MOUTH_OPEN_ANGLE - MOUTH_CLOSED_ANGLE) * level);
      if (targetAngle > MOUTH_OPEN_ANGLE) targetAngle = MOUTH_OPEN_ANGLE;
      if (targetAngle < MOUTH_CLOSED_ANGLE) targetAngle = MOUTH_CLOSED_ANGLE;
    }
    
    // ★ 核心逻辑：张开用平滑插值，闭合直接一步到位
    int newAngle;
    if (targetAngle > lastServoAngle) {
      // 张开：平滑插值，每次走差值的50%（但至少走2度）
      int diff = targetAngle - lastServoAngle;
      int step = diff / 2;
      if (step < 2) step = 2;
      if (step > diff) step = diff;
      newAngle = lastServoAngle + step;
    } else {
      // ★ 闭合：直接一步到位到目标角度！不做平滑！
      newAngle = targetAngle;
    }
    
    // 限幅
    if (newAngle < MOUTH_CLOSED_ANGLE) newAngle = MOUTH_CLOSED_ANGLE;
    if (newAngle > MOUTH_OPEN_ANGLE) newAngle = MOUTH_OPEN_ANGLE;
    
    // 只在角度变化时才写舵机（减少I2C通信）
    if (newAngle != lastServoAngle) {
      setServoAngle(MOUTH_SERVO_CH, newAngle);
      lastServoAngle = newAngle;
    }
    
    vTaskDelay(pdMS_TO_TICKS(20));  // 20ms更新周期
  }
}

// ====================================================================
// HTTP TTS 播放（从服务器获取 /stream.wav 并播放）
// ====================================================================
static inline void mono16_to_stereo32_msb(const int16_t* in, size_t nSamp, int32_t* outLR, float gain = 0.7f) {
  for (size_t i = 0; i < nSamp; ++i) {
    int32_t s = (int32_t)((float)in[i] * gain);
    if (s > 32767) s = 32767;
    if (s < -32768) s = -32768;
    int32_t v32 = s << 16;
    outLR[i*2 + 0] = v32;
    outLR[i*2 + 1] = v32;
  }
}

void taskHttpPlay(void*) {
  http_play_running = true;
  WiFiClient cli;
  
  // 等待服务器通知有音频可用
  Serial.println("[TTS] Task started, waiting for audio signal...");

  auto readLine = [&](String& out, uint32_t timeout_ms) -> bool {
    out = "";
    uint32_t t0 = millis();
    while (millis() - t0 < timeout_ms) {
      while (cli.available()) {
        char c = (char)cli.read();
        if (c == '\r') continue;
        if (c == '\n') return true;
        out += c;
        if (out.length() > 1024) return false;
      }
      delay(1);
    }
    return false;
  };

  auto readNRaw = [&](uint8_t* dst, size_t n, uint32_t timeout_ms) -> bool {
    size_t got = 0;
    uint32_t t0 = millis();
    while (got < n) {
      if (!cli.connected()) return false;
      int avail = cli.available();
      if (avail > 0) {
        int take = (int)min((size_t)avail, n - got);
        int r = cli.read(dst + got, take);
        if (r > 0) { got += r; continue; }
      }
      if (millis() - t0 > timeout_ms) return false;
      delay(1);
    }
    return true;
  };

  auto makeBodyReader = [&](bool& is_chunked, uint32_t& chunk_left) {
    return [&](uint8_t* dst, size_t n, uint32_t timeout_ms) -> bool {
      size_t filled = 0;
      uint32_t t0 = millis();
      while (filled < n) {
        if (!cli.connected()) return false;
        if (is_chunked) {
          if (chunk_left == 0) {
            String szLine;
            if (!readLine(szLine, timeout_ms)) return false;
            int sc = szLine.indexOf(';');
            if (sc >= 0) szLine = szLine.substring(0, sc);
            szLine.trim();
            uint32_t sz = 0;
            if (sscanf(szLine.c_str(), "%x", &sz) != 1) return false;
            if (sz == 0) { String dummy; readLine(dummy, 200); return false; }
            chunk_left = sz;
          }
          size_t need = (size_t)min<uint32_t>(chunk_left, (uint32_t)(n - filled));
          while (cli.available() < (int)need) {
            if (millis() - t0 > timeout_ms) return false;
            if (!cli.connected()) return false;
            delay(1);
          }
          int r = cli.read(dst + filled, need);
          if (r <= 0) {
            if (millis() - t0 > timeout_ms) return false;
            delay(1); continue;
          }
          filled     += r;
          chunk_left -= r;
          if (chunk_left == 0) {
            char crlf[2];
            if (!readNRaw((uint8_t*)crlf, 2, 200)) return false;
          }
        } else {
          if (!readNRaw(dst + filled, n - filled, timeout_ms)) return false;
          filled = n;
        }
      }
      return true;
    };
  };

  static int32_t outLR[1024 * 2];
  const uint32_t BODY_TIMEOUT_MS = 1500;

  // 等待 WiFi 连接成功
  Serial.println("[TTS] Waiting for WiFi...");
  while (WiFi.status() != WL_CONNECTED) {
    delay(200);
    if (!http_play_running) { vTaskDelete(NULL); return; }
  }
  Serial.println("[TTS] WiFi ready, task starting");

  while (http_play_running) {
    // 等待音频可用信号
    if (!tts_audio_available) {
      vTaskDelay(pdMS_TO_TICKS(100));
      continue;
    }
    
    // 每次循环都检查 WiFi 状态
    if (WiFi.status() != WL_CONNECTED) {
      delay(500);
      continue;
    }
    
    // TTS 优先：如果本地音效正在播放，先停止
    if (local_sound_playing) {
      local_sound_stop_requested = true;
      int wait = 50;
      while (local_sound_playing && wait-- > 0) vTaskDelay(pdMS_TO_TICKS(10));
    }
    
    mic_enabled = false;
    Serial.println("[TTS] Preparing to play audio...");
    
    if (!cli.connected()) {
      Serial.println("[TTS] HTTP connect...");
      if (!cli.connect(SERVER_HOST, SERVER_PORT)) { 
        tts_audio_available = false;
        mic_enabled = true;
        delay(500); 
        continue; 
      }
      String req =
        String("GET /stream.wav HTTP/1.1\r\n") +
        "Host: " + SERVER_HOST + ":" + String(SERVER_PORT) + "\r\n" +
        "Connection: keep-alive\r\n\r\n";
      cli.print(req);
    }

    bool header_ok = false;
    bool is_chunked = false;
    uint32_t content_len = 0;
    {
      String line; uint32_t t0 = millis();
      while (millis() - t0 < 3000) {
        if (!readLine(line, 1000)) { if (!cli.connected()) break; continue; }
        String u = line; u.toLowerCase();
        if (u.startsWith("transfer-encoding:")) { if (u.indexOf("chunked") >= 0) is_chunked = true; }
        else if (u.startsWith("content-length:")) { content_len = (uint32_t) strtoul(u.substring(strlen("content-length:")).c_str(), nullptr, 10); }
        if (line.length() == 0) { header_ok = true; break; }
      }
    }
    if (!header_ok) { cli.stop(); delay(300); continue; }

    uint32_t chunk_left = 0;
    auto readBody = makeBodyReader(is_chunked, chunk_left);

    uint8_t hdr12[12];
    if (!readBody(hdr12, 12, 1000)) { cli.stop(); delay(300); continue; }
    if (memcmp(hdr12, "RIFF", 4) != 0 || memcmp(hdr12 + 8, "WAVE", 4) != 0) { cli.stop(); delay(300); continue; }

    bool gotFmt = false, gotData = false;
    uint8_t chdr[8];
    uint16_t audioFormat=0, numChannels=0, bitsPerSample=0;
    uint32_t sampleRate=0;

    while (!gotData) {
      if (!readBody(chdr, 8, 1000)) { cli.stop(); delay(300); goto reconnect; }
      uint32_t sz = (uint32_t)chdr[4] | ((uint32_t)chdr[5]<<8) | ((uint32_t)chdr[6]<<16) | ((uint32_t)chdr[7]<<24);

      if (memcmp(chdr, "fmt ", 4) == 0) {
        if (sz < 16) { cli.stop(); delay(300); goto reconnect; }
        uint8_t fmtbuf[32];
        size_t toread = min(sz, (uint32_t)sizeof(fmtbuf));
        if (!readBody(fmtbuf, toread, 1000)) { cli.stop(); delay(300); goto reconnect; }
        if (sz > toread) {
          size_t left = sz - toread;
          while (left) { uint8_t dump[128]; size_t d = min(left, sizeof(dump));
            if (!readBody(dump, d, 1000)) { cli.stop(); delay(300); goto reconnect; }
            left -= d;
          }
        }
        audioFormat   = (uint16_t)(fmtbuf[0] | (fmtbuf[1] << 8));
        numChannels   = (uint16_t)(fmtbuf[2] | (fmtbuf[3] << 8));
        sampleRate    = (uint32_t)(fmtbuf[4] | (fmtbuf[5] << 8) | (fmtbuf[6] << 16) | (fmtbuf[7] << 24));
        bitsPerSample = (uint16_t)(fmtbuf[14] | (fmtbuf[15] << 8));
        gotFmt = true;
      }
      else if (memcmp(chdr, "data", 4) == 0) {
        if (!gotFmt) { cli.stop(); delay(300); goto reconnect; }
        gotData = true;
      }
      else {
        size_t left = sz;
        while (left) { uint8_t dump[128]; size_t d = min(left, sizeof(dump));
          if (!readBody(dump, d, 1000)) { cli.stop(); delay(300); goto reconnect; }
          left -= d;
        }
      }
    }

    if (!(audioFormat==1 && numChannels==1 && bitsPerSample==16 &&
          (sampleRate==8000 || sampleRate==12000 || sampleRate==16000 || sampleRate==24000))) {
      Serial.printf("[TTS] unsupported fmt: ch=%u bits=%u sr=%u\n", numChannels, bitsPerSample, sampleRate);
      cli.stop(); delay(300); continue;
    }
    Serial.printf("[TTS] WAV ok: %u/16bit/mono (chunked=%d)\n", sampleRate, is_chunked ? 1 : 0);

    // 重新配置扬声器采样率（如果需要）
    static uint32_t current_out_rate = 0;
    if (current_out_rate != sampleRate) {
      i2sOut.end();
      i2sOut.setPins(I2S_SPK_BCLK, I2S_SPK_LRCK, I2S_SPK_DIN);
      if (!i2sOut.begin(I2S_MODE_STD, (int)sampleRate, I2S_DATA_BIT_WIDTH_32BIT, I2S_SLOT_MODE_STEREO)) {
        Serial.println("[I2S OUT] reconfig failed!");
        cli.stop();
        tts_audio_available = false;
        mic_enabled = true;
        delay(300);
        goto reconnect;
      }
      current_out_rate = sampleRate;
      Serial.printf("[I2S OUT] Configured at %u Hz\n", sampleRate);
    }

    tts_playing = true;
    mic_enabled = false;  // 播放时禁用麦克风（共用引脚）
    
    // 启动嘴巴动画
    requestMouthStart();

    while (http_play_running) {
      uint8_t inbuf[2048];
      size_t filled = 0;

      uint32_t bytes20 = (sampleRate * 2 * 20) / 1000;
      if (bytes20 < 2) bytes20 = 2;

      if (!readBody(inbuf, bytes20, BODY_TIMEOUT_MS)) { break; }
      filled = bytes20;

      while (filled + bytes20 <= sizeof(inbuf)) {
        if (!readBody(inbuf + filled, bytes20, 2)) { break; }
        filled += bytes20;
      }

      if (filled & 1) filled -= 1;
      if (filled == 0) { vTaskDelay(pdMS_TO_TICKS(1)); continue; }

      size_t samp = filled / 2;
      
      // ★ 从音频样本计算音量级别，驱动嘴巴舵机
      feedMouthLevelFromSamples((const int16_t*)inbuf, samp);
      
      mono16_to_stereo32_msb((const int16_t*)inbuf, samp, outLR, 0.7f);

      size_t bytes = samp * 2 * sizeof(int32_t);
      size_t off = 0;
      while (off < bytes && http_play_running) {
        size_t wrote = i2sOut.write((uint8_t*)outLR + off, bytes - off);
        if (wrote == 0) vTaskDelay(pdMS_TO_TICKS(1));
        else off += wrote;
      }
    }

    tts_playing = false;
    tts_audio_available = false;  // 重置标志
    
    // 停止嘴巴动画
    requestMouthIdle();
    
    // 播放结束，恢复麦克风上传
    Serial.println("[TTS] Playback done");
    mic_enabled = true;

    reconnect:
    if (!cli.connected()) {
      Serial.println("[TTS] Disconnected");
      tts_audio_available = false;
      mic_enabled = true;
    }
  }

  tts_playing = false;
  tts_audio_available = false;
  mic_enabled = true;
  vTaskDelete(NULL);
}

// ====================================================================
// 摄像头任务
// ====================================================================
void taskCamCapture(void*) {
  for(;;) {
    // 只要WebSocket连接就发送，不受systemMode限制
    if (cam_ws_ready) {
      camera_fb_t* fb = esp_camera_fb_get();
      if (fb && fb->format == PIXFORMAT_JPEG) {
        frame_captured_count++;
        if (xQueueSend(qFrames, &fb, 0) != pdPASS) {
          fb_ptr_t drop = nullptr;
          if (xQueueReceive(qFrames, &drop, 0) == pdPASS && drop) {
            esp_camera_fb_return(drop);
            frame_dropped_count++;
          }
          xQueueSend(qFrames, &fb, 0);
        }
      } else if (fb) {
        esp_camera_fb_return(fb);
      }
    }
    vTaskDelay(pdMS_TO_TICKS(10));  // 稍微降低频率避免资源竞争
  }
}

void taskCamSend(void*) {
  for(;;) {
    fb_ptr_t fb = nullptr;
    if (xQueueReceive(qFrames, &fb, pdMS_TO_TICKS(100)) == pdPASS) {
      if (fb && cam_ws_ready) {
        bool ok = wsCam.sendBinary((const char*)fb->buf, fb->len);
        if (ok) {
          frame_sent_count++;
        } else {
          wsCam.close();
          cam_ws_ready = false;
        }
        esp_camera_fb_return(fb);
      } else if (fb) {
        esp_camera_fb_return(fb);
      }
    }
  }
}

// ====================================================================
// 麦克风任务
// ====================================================================
static uint32_t mic_chunk_count = 0;
static uint32_t last_mic_log_time = 0;

void taskMicCapture(void*) {
  const int samples_per_chunk = BYTES_PER_CHUNK / 2;
  Serial.println("[MIC] Capture task started");
  
  for(;;) {
    if (run_audio_stream && aud_ws_ready && mic_enabled) {
      AudioChunk ch;
      ch.n = BYTES_PER_CHUNK;
      int16_t* out = reinterpret_cast<int16_t*>(ch.data);
      int i = 0;
      int retry_count = 0;
      while (i < samples_per_chunk && retry_count < 1000) {
        int v = i2sIn.read();
        if (v == -1) { 
          delay(1); 
          retry_count++;
          continue; 
        }
        out[i++] = (int16_t)v;
        retry_count = 0;
      }
      
      if (i == samples_per_chunk) {
        if (xQueueSend(qAudio, &ch, 0) != pdPASS) {
          AudioChunk dump;
          xQueueReceive(qAudio, &dump, 0);
          xQueueSend(qAudio, &ch, 0);
        }
        mic_chunk_count++;
        
        // 每5秒打印一次状态
        if (millis() - last_mic_log_time > 5000) {
          Serial.printf("[MIC] Captured %u chunks\n", mic_chunk_count);
          last_mic_log_time = millis();
        }
      } else {
        Serial.println("[MIC] Warning: Failed to read samples");
      }
    } else {
      vTaskDelay(pdMS_TO_TICKS(10));
    }
  }
}

static uint32_t mic_upload_count = 0;
static uint32_t last_upload_log_time = 0;

void taskMicUpload(void*) {
  Serial.println("[MIC] Upload task started");
  
  for(;;) {
    if (run_audio_stream && aud_ws_ready) {
      AudioChunk ch;
      if (xQueueReceive(qAudio, &ch, pdMS_TO_TICKS(100)) == pdPASS) {
        bool ok = wsAud.sendBinary((const char*)ch.data, ch.n);
        if (ok) {
          mic_upload_count++;
          // 每5秒打印一次状态
          if (millis() - last_upload_log_time > 5000) {
            Serial.printf("[MIC] Uploaded %u chunks\n", mic_upload_count);
            last_upload_log_time = millis();
          }
        }
      }
    } else {
      vTaskDelay(pdMS_TO_TICKS(10));
    }
  }
}

// ====================================================================
// HTTP 处理函数
// ====================================================================
void handleRoot() {
  String html = R"rawliteral(<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ESP32 Control</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:'Segoe UI',sans-serif;background:#1a1a2e;color:#eee;padding:10px}
h1{color:#ffd700;margin-bottom:10px;font-size:18px;text-align:center}
.container{display:flex;flex-wrap:wrap;gap:10px;justify-content:center}
.panel{background:#16213e;border-radius:8px;padding:10px;min-width:280px;flex:1;max-width:400px}
.panel h2{color:#00d4ff;font-size:14px;border-bottom:1px solid #333;padding-bottom:5px;margin-bottom:8px}
.servo-row{display:flex;align-items:center;margin-bottom:5px}
.servo-label{width:60px;font-size:11px}
.servo-slider{flex:1;margin:0 5px}
.servo-value{width:30px;text-align:center;font-size:11px}
.btn{background:#ffd700;color:#000;border:none;padding:6px 12px;border-radius:4px;cursor:pointer;font-weight:bold;margin:2px;font-size:11px}
.btn:hover{background:#ffed4a}
.btn-blue{background:#00d4ff}
.btn-green{background:#00ff88}
.btn-red{background:#ff4757;color:#fff}
#video{width:100%;max-width:320px;background:#000;border-radius:4px}
#status{color:#888;font-size:11px;margin-top:5px}
.asr-text{background:#0a0a1a;padding:8px;border-radius:4px;min-height:60px;font-size:12px;margin-top:8px}
</style>
</head>
<body>
<h1>ESP32 Integrated Controller</h1>
<div class="container">
<div class="panel">
<h2>Camera</h2>
<canvas id="video" width="320" height="240"></canvas>
<div id="status">Connecting...</div>
<div style="margin-top:5px">
<button class="btn btn-blue" onclick="cc()">Connect</button>
<button class="btn btn-red" onclick="dc()">Disconnect</button>
</div>
</div>
<div class="panel">
<h2>Voice</h2>
<div class="asr-text" id="asrText">Waiting...</div>
<div style="margin-top:5px">
<button class="btn btn-green" onclick="sa()">Start ASR</button>
<button class="btn btn-red" onclick="xa()">Stop</button>
</div>
</div>
<div class="panel">
<h2>Mouth, Ears & Tail</h2>
<div id="earTail"></div>
</div>
<div class="panel">
<h2>PCA9685 Servos</h2>
<div id="pcaServos"></div>
</div>
<div class="panel">
<h2>STS3032 Servos</h2>
<div id="stsServos"></div>
<div style="margin-top:5px">
<button class="btn" onclick="scanSTS()">Scan IDs</button>
</div>
</div>
<div class="panel">
<h2>Animations</h2>
<div id="animButtons"></div>
<button class="btn btn-red" onclick="xa2()">Stop</button>
</div>
</div>
<script>
var wv=null,wu=null,cv=document.getElementById('video'),cx=cv.getContext('2d'),st=document.getElementById('status'),at=document.getElementById('asrText');
function is(){var et=document.getElementById('earTail');var etc=[{ch:12,lb:'Mouth'},{ch:13,lb:'Tail'},{ch:14,lb:'L Ear'},{ch:15,lb:'R Ear'}];for(var k=0;k<etc.length;k++){(function(c){var r=document.createElement('div');r.className='servo-row';r.innerHTML='<span class="servo-label">'+c.lb+'</span><input type="range" class="servo-slider" min="0" max="180" value="90" oninput="sp('+c.ch+',this.value)"><span class="servo-value" id="pca'+c.ch+'">90</span>';et.appendChild(r);})(etc[k]);}var p=document.getElementById('pcaServos');for(var i=0;i<8;i++){var r=document.createElement('div');r.className='servo-row';r.innerHTML='<span class="servo-label">CH'+i+'</span><input type="range" class="servo-slider" min="0" max="180" value="90" oninput="sp('+i+',this.value)"><span class="servo-value" id="pca'+i+'">90</span>';p.appendChild(r);}var s=document.getElementById('stsServos');for(var j=1;j<=3;j++){var r2=document.createElement('div');r2.className='servo-row';r2.innerHTML='<span class="servo-label">ID '+j+'</span><input type="range" class="servo-slider" min="0" max="4095" value="2048" oninput="ss('+j+',this.value)"><span class="servo-value" id="sts'+j+'">2048</span>';s.appendChild(r2);}}
function sp(c,a){document.getElementById('pca'+c).textContent=a;fetch('/servo?ch='+c+'&angle='+a);}
function ss(i,p){document.getElementById('sts'+i).textContent=p;fetch('/sts?id='+i+'&pos='+p);}
function scanSTS(){fetch('/sts?scan=1').then(function(r){return r.text();}).then(function(t){alert(t);});}
function pa(n){fetch('/anim?play='+n);}
function xa2(){fetch('/anim?stop=1');}
function loadAnimButtons(){fetch('/animlist').then(function(r){return r.json();}).then(function(d){var ab=document.getElementById('animButtons');ab.innerHTML='';if(d.anims&&d.anims.length>0){for(var i=0;i<d.anims.length;i++){var btn=document.createElement('button');btn.className='btn';btn.textContent='Anim '+d.anims[i].id;btn.onclick=function(n){return function(){pa(n);};}(d.anims[i].id);ab.appendChild(btn);}}else{ab.innerHTML='<span style=\"color:#888;font-size:11px\">No animations found</span>';}});}
function cc(){var h=location.hostname||'192.168.2.7';wv=new WebSocket('ws://'+h+':8081/ws/viewer');wv.binaryType='arraybuffer';wv.onopen=function(){st.textContent='Connected';};wv.onclose=function(){st.textContent='Disconnected';};wv.onmessage=function(e){if(e.data instanceof ArrayBuffer){var b=new Blob([e.data],{type:'image/jpeg'});var u=URL.createObjectURL(b);var m=new Image();m.onload=function(){cx.drawImage(m,0,0,cv.width,cv.height);URL.revokeObjectURL(u);};m.src=u;}};}
function dc(){if(wv)wv.close();}
function sa(){var h=location.hostname||'192.168.2.7';wu=new WebSocket('ws://'+h+':8081/ws_ui');wu.onmessage=function(e){var d=e.data;if(d.indexOf('PARTIAL:')==0){at.textContent=d.substring(8)||'...';}else if(d.indexOf('FINAL:')==0){at.textContent=d.substring(6);}};}
function xa(){if(wu)wu.close();at.textContent='Stopped';}
is();loadAnimButtons();setTimeout(cc,500);
</script>
</body>
</html>)rawliteral";
  server.send(200, "text/html", html);
}

void handleServo() {
  if (!server.hasArg("ch") || !server.hasArg("angle")) {
    server.send(400, "text/plain", "Missing ch or angle");
    return;
  }
  int ch = server.arg("ch").toInt();
  int angle = server.arg("angle").toInt();
  if (ch < 0 || ch > 15) {
    server.send(400, "text/plain", "ch must be 0-15");
    return;
  }
  setServoAngle((uint8_t)ch, angle);
  server.send(200, "text/plain", "OK");
}

void handleSTS() {
  // 添加 CORS 头，允许跨域访问
  server.sendHeader("Access-Control-Allow-Origin", "*");
  server.sendHeader("Access-Control-Allow-Methods", "GET, POST, OPTIONS");
  server.sendHeader("Access-Control-Allow-Headers", "Content-Type");
  
  if (server.hasArg("scan")) {
    // 扫描 STS3032
    String result = "Found IDs: ";
    int count = 0;
    for (int id = 1; id <= 20; id++) {
      if (sts.Ping(id) != -1) {
        if (count > 0) result += ", ";
        result += String(id);
        count++;
      }
      delay(5);
    }
    if (count == 0) result = "No servos found";
    server.send(200, "text/plain", result);
    return;
  }
  
  if (!server.hasArg("id") || !server.hasArg("pos")) {
    server.send(400, "text/plain", "Missing id or pos");
    return;
  }
  int id = server.arg("id").toInt();
  int pos = server.arg("pos").toInt();
  
  // 支持速度和加速度参数，使用默认值如果未提供
  int speed = 500;  // 默认速度 500ms
  int accel = 50;   // 默认加速度 50
  if (server.hasArg("speed")) {
    speed = server.arg("speed").toInt();
    speed = constrain(speed, 0, 5000);
  }
  if (server.hasArg("accel")) {
    accel = server.arg("accel").toInt();
    accel = constrain(accel, 0, 254);
  }
  
  sts.WritePosEx(id, pos, speed, accel);
  Serial.printf("[HTTP] STS ID%d -> pos:%d speed:%d accel:%d\n", id, pos, speed, accel);
  server.send(200, "text/plain", "OK");
}

void handleAnim() {
  if (server.hasArg("stop")) {
    currentAnim = 0;
    animLoop = false;
    switchToIdleOrOffline();
    server.send(200, "text/plain", "Stopped");
    return;
  }
  if (server.hasArg("loop")) {
    // 启动循环播放所有动画
    animLoop = true;
    for (int i = 1; i <= maxAnimIndex; i++) {
      if (ANIM_FRAMES[i] > 0) {
        currentAnim = i;
        currentFrame = 1;
        lastFrameTime = 0;
        systemMode = MODE_ANIMATION;
        break;
      }
    }
    server.send(200, "text/plain", "Loop started");
    return;
  }
  if (server.hasArg("play")) {
    int n = server.arg("play").toInt();
    if (n >= 1 && n <= maxAnimIndex && ANIM_FRAMES[n] > 0) {
      animLoop = server.hasArg("repeat");  // 如果有repeat参数则循环单个动画
      currentAnim = n;
      currentFrame = 1;
      lastFrameTime = 0;
      systemMode = MODE_ANIMATION;
      server.send(200, "text/plain", "Playing anim " + String(n));
    } else {
      server.send(400, "text/plain", "Invalid anim (max: " + String(maxAnimIndex) + ")");
    }
    return;
  }
  server.send(400, "text/plain", "No action");
}

void handleStatus() {
  String json = "{";
  json += "\"mode\":" + String(systemMode) + ",";
  json += "\"wifi\":\"" + WiFi.localIP().toString() + "\",";
  json += "\"cam_ws\":" + String(cam_ws_ready ? "true" : "false") + ",";
  json += "\"aud_ws\":" + String(aud_ws_ready ? "true" : "false") + ",";
  json += "\"frames_sent\":" + String(frame_sent_count) + ",";
  json += "\"gait\":" + String(currentGait) + ",";
  json += "\"turn\":" + String(turnFactor);
  json += "}";
  server.send(200, "application/json", json);
}

void handleAnimList() {
  // 添加 CORS 头
  server.sendHeader("Access-Control-Allow-Origin", "*");
  server.sendHeader("Access-Control-Allow-Methods", "GET, POST, OPTIONS");
  
  // 返回可用动画列表
  String json = "{\"anims\":[";
  bool first = true;
  for (int i = 1; i <= maxAnimIndex; i++) {
    if (ANIM_FRAMES[i] > 0) {
      if (!first) json += ",";
      first = false;
      json += "{\"id\":" + String(i) + ",\"frames\":" + String(ANIM_FRAMES[i]) + "}";
    }
  }
  json += "],\"max\":" + String(maxAnimIndex) + ",\"frameDelayMs\":" + String(FRAME_DELAY) + "}";
  server.send(200, "application/json", json);
}

void handleSoundList() {
  server.sendHeader("Access-Control-Allow-Origin", "*");
  server.sendHeader("Access-Control-Allow-Methods", "GET, POST, OPTIONS");
  
  String json = "{\"sounds\":[";
  bool first = true;
  for (int i = 1; i <= maxSoundIndex; i++) {
    if (SOUND_NUM_SAMPLES[i] > 0) {
      if (!first) json += ",";
      first = false;
      float dur = (float)SOUND_NUM_SAMPLES[i] / SOUND_SAMPLE_RATES[i];
      json += "{\"id\":" + String(i);
      json += ",\"rate\":" + String(SOUND_SAMPLE_RATES[i]);
      json += ",\"duration\":" + String(dur, 2) + "}";
    }
  }
  json += "],\"max\":" + String(maxSoundIndex) + "}";
  server.send(200, "application/json", json);
}

void handleGait() {
  // 添加 CORS 头
  server.sendHeader("Access-Control-Allow-Origin", "*");
  server.sendHeader("Access-Control-Allow-Methods", "GET, POST, OPTIONS");
  
  if (server.hasArg("mode")) {
    String mode = server.arg("mode");
    mode.toUpperCase();
    
    if (mode == "WALK") {
      startGait(GAIT_WALK);
      server.send(200, "text/plain", "WALK started");
    } else if (mode == "TROT") {
      startGait(GAIT_TROT);
      server.send(200, "text/plain", "TROT started");
    } else if (mode == "RUN") {
      startGait(GAIT_RUN);
      server.send(200, "text/plain", "RUN started");
    } else if (mode == "IDLE") {
      startGait(GAIT_IDLE);
      server.send(200, "text/plain", "IDLE started");
    } else if (mode == "SIT") {
      startGait(GAIT_SIT);
      server.send(200, "text/plain", "SIT started");
    } else if (mode == "JUMP") {
      startGait(GAIT_JUMP);
      server.send(200, "text/plain", "JUMP started");
    } else if (mode == "TROT_STRAIGHT" || mode == "TS") {
      startGait(GAIT_TROT_STRAIGHT);
      server.send(200, "text/plain", "TROT_STRAIGHT started");
    } else if (mode == "BACKWARD" || mode == "BACK") {
      startGait(GAIT_BACKWARD);
      server.send(200, "text/plain", "BACKWARD started");
    } else if (mode == "EFFICIENT_WALK" || mode == "EFF" || mode == "EFFWALK") {
      startGait(GAIT_EFFICIENT_WALK);
      server.send(200, "text/plain", "EFFICIENT_WALK started");
    } else if (mode == "WAVE") {
      startGait(GAIT_WAVE);
      server.send(200, "text/plain", "WAVE started");
    } else if (mode == "LAYDOWN" || mode == "LAY") {
      startGait(GAIT_LAYDOWN);
      server.send(200, "text/plain", "LAYDOWN started");
    } else if (mode == "NEWYEAR" || mode == "NY") {
      startGait(GAIT_NEWYEAR);
      server.send(200, "text/plain", "NEWYEAR started");
    } else if (mode == "STUMBLE" || mode == "FALL") {
      startGait(GAIT_STUMBLE);
      server.send(200, "text/plain", "STUMBLE started");
    } else if (mode == "STOP") {
      stopGait();
      server.send(200, "text/plain", "STOPPED");
    } else if (mode == "CENTER") {
      stopGait();
      resetLegsToCenter();
      server.send(200, "text/plain", "CENTERED");
    } else {
      server.send(400, "text/plain", "Unknown mode: " + mode);
    }
    return;
  }
  
  if (server.hasArg("turn")) {
    float t = server.arg("turn").toFloat();
    turnFactor = constrain(t, -1.0f, 1.0f);
    server.send(200, "text/plain", "Turn: " + String(turnFactor));
    return;
  }
  
  // 返回当前状态
  String json = "{\"gait\":" + String(currentGait) + ",\"turn\":" + String(turnFactor) + "}";
  server.send(200, "application/json", json);
}

// ====================================================================
// WebSocket 回调
// ====================================================================
void setupWebSocketCallbacks() {
  wsCam.onEvent([](WebsocketsEvent ev, String) {
    if (ev == WebsocketsEvent::ConnectionOpened) {
      cam_ws_ready = true;
      Serial.println("[WS-CAM] Connected");
      statusLine2 = "Camera: Connected";
    }
    if (ev == WebsocketsEvent::ConnectionClosed) {
      cam_ws_ready = false;
      Serial.println("[WS-CAM] Disconnected");
      statusLine2 = "Camera: Disconnected";
    }
  });

  wsCam.onMessage([](WebsocketsMessage msg) {
    if (msg.isBinary()) {
      if (screenPageMode != SCREEN_PAGE_EXPRESSION) {
        updateRemoteScreenFrame((const uint8_t *)msg.c_str(), msg.length());
      }
      return;
    }
    if (!msg.isText()) return;
    String s = msg.data();
    s.trim();
    if (s.startsWith("SCRMODE:")) {
      int mode = constrain(s.substring(8).toInt(), 0, 2);
      setScreenPageMode((ScreenPageMode)mode);
      return;
    }
  });

  wsAud.onEvent([](WebsocketsEvent ev, String) {
    if (ev == WebsocketsEvent::ConnectionOpened) {
      aud_ws_ready = true;
      Serial.println("[WS-AUD] Connected");
      statusLine3 = "Audio: Connected";
    }
    if (ev == WebsocketsEvent::ConnectionClosed) {
      aud_ws_ready = false;
      run_audio_stream = false;
      Serial.println("[WS-AUD] Disconnected");
      statusLine3 = "Audio: Disconnected";
    }
  });

  wsAud.onMessage([](WebsocketsMessage msg) {
    if (!msg.isText()) return;
    String s = msg.data();
    s.trim();
    
    if (s == "START") {
      run_audio_stream = true;
      mic_enabled = true;
      xQueueReset(qAudio);
      wsAud.send("OK:STARTED");
      Serial.println("[WS-AUD] ASR Started");
    } else if (s == "STOP") {
      run_audio_stream = false;
      wsAud.send("OK:STOPPED");
      Serial.println("[WS-AUD] ASR Stopped");
    } else if (s == "RESTART") {
      run_audio_stream = false;
      xQueueReset(qAudio);
      delay(50);
      run_audio_stream = true;
      wsAud.send("START");
    } else if (s == "RESET") {
      run_audio_stream = false;
      mic_enabled = false;
      xQueueReset(qAudio);
      Serial.println("[WS-AUD] System Reset");
    } else if (s == "TTS_START") {
      // 服务器通知开始TTS播放
      run_audio_stream = false;
      mic_enabled = false;
      tts_audio_available = true;
      Serial.println("[WS-AUD] TTS Start signal received");
    } else if (s == "TTS_STOP") {
      // 服务器通知TTS播放结束
      tts_audio_available = false;
      Serial.println("[WS-AUD] TTS Stop signal received");
    } else if (s.startsWith("SCRMODE:")) {
      int mode = constrain(s.substring(8).toInt(), 0, 2);
      setScreenPageMode((ScreenPageMode)mode);
    }
    // ---- PCA9685 舵机控制: SERVO:ch,angle ----
    else if (s.startsWith("SERVO:")) {
      String params = s.substring(6);
      int comma = params.indexOf(',');
      if (comma > 0) {
        int ch = params.substring(0, comma).toInt();
        int angle = params.substring(comma + 1).toInt();
        if (ch >= 0 && ch <= 15) {
          setServoAngle((uint8_t)ch, angle);
          Serial.printf("[WS] Servo CH%d -> %d\n", ch, angle);
        }
      }
    }
    // ---- STS3032 舵机控制: STS:id,pos,speed,accel 或 STS:SCAN ----
    else if (s.startsWith("STS:")) {
      String params = s.substring(4);
      if (params == "SCAN") {
        String result = "STS Found: ";
        int count = 0;
        for (int id = 1; id <= 20; id++) {
          if (sts.Ping(id) != -1) {
            if (count > 0) result += ", ";
            result += String(id);
            count++;
          }
          delay(5);
        }
        if (count == 0) result = "No STS servos found";
        wsAud.send(result);
        Serial.println(result);
      } else {
        // 解析参数: id,pos,speed,accel (speed和accel可选)
        int c1 = params.indexOf(',');
        int c2 = params.indexOf(',', c1 + 1);
        int c3 = params.indexOf(',', c2 + 1);
        
        if (c1 > 0) {
          int id = params.substring(0, c1).toInt();
          int pos = (c2 > 0) ? params.substring(c1 + 1, c2).toInt() : params.substring(c1 + 1).toInt();
          int speed = 500;  // 默认值
          int accel = 50;   // 默认值
          
          if (c2 > 0) {
            speed = (c3 > 0) ? params.substring(c2 + 1, c3).toInt() : params.substring(c2 + 1).toInt();
            speed = constrain(speed, 0, 5000);
          }
          if (c3 > 0) {
            accel = params.substring(c3 + 1).toInt();
            accel = constrain(accel, 0, 254);
          }
          
          sts.WritePosEx(id, pos, speed, accel);
          Serial.printf("[WS] STS ID%d -> pos:%d speed:%d accel:%d\n", id, pos, speed, accel);
        }
      }
    }
    // ---- 屏幕测试: SCREEN:color,r,g,b ----
    else if (s.startsWith("SCREEN:")) {
      String params = s.substring(7);
      int c1 = params.indexOf(',');
      int c2 = params.indexOf(',', c1 + 1);
      int c3 = params.indexOf(',', c2 + 1);
      if (c1 > 0 && c2 > 0 && c3 > 0) {
        int r = params.substring(c1 + 1, c2).toInt();
        int g = params.substring(c2 + 1, c3).toInt();
        int b = params.substring(c3 + 1).toInt();
        uint16_t color = tft.color565(r, g, b);
        tft.fillScreen(color);
        Serial.printf("[WS] Screen color: R%d G%d B%d\n", r, g, b);
      }
    }
    // ---- 摄像头参数: CAMSET:param,value ----
    else if (s.startsWith("CAMSET:")) {
      String params = s.substring(7);
      int comma = params.indexOf(',');
      if (comma > 0) {
        String param = params.substring(0, comma);
        int value = params.substring(comma + 1).toInt();
        sensor_t * sensor = esp_camera_sensor_get();
        if (sensor) {
          if (param == "brightness") {
            sensor->set_brightness(sensor, constrain(value, -2, 2));
          } else if (param == "contrast") {
            sensor->set_contrast(sensor, constrain(value, -2, 2));
          } else if (param == "saturation") {
            sensor->set_saturation(sensor, constrain(value, -2, 2));
          } else if (param == "sharpness") {
            sensor->set_sharpness(sensor, constrain(value, -2, 2));
          } else if (param == "wb_mode") {
            // 0=自动, 1=晴天, 2=阴天, 3=办公室, 4=家
            sensor->set_wb_mode(sensor, constrain(value, 0, 4));
          } else if (param == "ae_level") {
            // 自动曝光补偿等级 (-2 to 2)
            sensor->set_ae_level(sensor, constrain(value, -2, 2));
          } else if (param == "special_effect") {
            // 0=无, 1=负片, 2=黑白, 3=红色, 4=绿色, 5=蓝色, 6=复古
            sensor->set_special_effect(sensor, constrain(value, 0, 6));
          } else if (param == "hmirror") {
            sensor->set_hmirror(sensor, value ? 1 : 0);
          } else if (param == "vflip") {
            sensor->set_vflip(sensor, value ? 1 : 0);
          }
          Serial.printf("[CAMSET] %s = %d\n", param.c_str(), value);
          wsAud.send("OK:CAMSET");
        } else {
          Serial.println("[CAMSET] Camera sensor not available");
          wsAud.send("ERR:NO_SENSOR");
        }
      }
    }
    // ---- TFT屏幕设置: TFTSET:param,value ----
    else if (s.startsWith("TFTSET:")) {
      String params = s.substring(7);
      int comma = params.indexOf(',');
      if (comma > 0) {
        String param = params.substring(0, comma);
        int value = params.substring(comma + 1).toInt();
        
        if (param == "brightness") {
          // ST7789 WRDISBV (0x51) 设置显示亮度 0-255
          // 先启用亮度控制: WRCTRLD (0x53)
          uint8_t ctrl = 0x2C;  // BL on, DD on, BCtrl on
          tft.sendCommand(0x53, &ctrl, 1);
          uint8_t bri = (uint8_t)constrain(value, 0, 255);
          tft.sendCommand(0x51, &bri, 1);
          Serial.printf("[TFTSET] brightness = %d\n", bri);
        } else if (param == "invert") {
          tft.invertDisplay(value != 0);
          Serial.printf("[TFTSET] invert = %d\n", value);
        } else if (param == "gamma") {
          // Gamma曲线选择: 1=G2.2(默认), 2=G1.8, 4=G2.5, 8=G1.0
          uint8_t g = (uint8_t)constrain(value, 1, 8);
          tft.sendCommand(0x26, &g, 1);
          Serial.printf("[TFTSET] gamma = %d\n", g);
        } else if (param == "display") {
          tft.enableDisplay(value != 0);
          Serial.printf("[TFTSET] display = %d\n", value);
        } else if (param == "rotation") {
          tft.setRotation(constrain(value, 0, 3));
          Serial.printf("[TFTSET] rotation = %d\n", value);
        } else if (param == "cabc") {
          // Content Adaptive Brightness Control (0x55)
          // 0=Off, 1=UI模式, 2=静态图像, 3=动态视频
          uint8_t cabc = (uint8_t)constrain(value, 0, 3);
          tft.sendCommand(0x55, &cabc, 1);
          Serial.printf("[TFTSET] CABC = %d\n", cabc);
        }
        wsAud.send("OK:TFTSET");
      }
    }
    // ---- 音频测试: AUDIO_TEST:freq,dur ----
    else if (s.startsWith("AUDIO_TEST:")) {
      String params = s.substring(11);
      int comma = params.indexOf(',');
      if (comma > 0) {
        int freq = params.substring(0, comma).toInt();
        int dur = params.substring(comma + 1).toInt();
        Serial.printf("[WS] Audio test: %dHz, %dms\n", freq, dur);
        
        // 生成简单的正弦波测试音（引脚已分开，不影响麦克风）
        const int samples = (16000 * dur) / 1000;
        const int chunkSize = 512;
        int16_t buf[chunkSize];
        float phase = 0;
        float inc = 2.0f * 3.14159f * freq / 16000.0f;
        int32_t outBuf[chunkSize * 2];
        
        for (int i = 0; i < samples; i += chunkSize) {
          int n = min(chunkSize, samples - i);
          for (int j = 0; j < n; j++) {
            buf[j] = (int16_t)(sin(phase) * 16000);
            phase += inc;
          }
          // 转换为立体声32位
          for (int j = 0; j < n; j++) {
            int32_t v = buf[j] << 16;
            outBuf[j * 2] = v;
            outBuf[j * 2 + 1] = v;
          }
          i2sOut.write((uint8_t*)outBuf, n * 2 * sizeof(int32_t));
        }
        Serial.println("[WS] Audio test done");
      }
    }
    // ---- 动画控制: ANIM:n 或 ANIM:STOP ----
    else if (s.startsWith("ANIM:")) {
      String params = s.substring(5);
      if (params == "STOP") {
        currentAnim = 0;
        animLoop = false;
        switchToIdleOrOffline();
        Serial.println("[WS] Animation stopped");
      } else {
        int n = params.toInt();
        if (n >= 1 && n <= maxAnimIndex && ANIM_FRAMES[n] > 0) {
          currentAnim = n;
          currentFrame = 1;
          lastFrameTime = 0;
          systemMode = MODE_ANIMATION;
          Serial.printf("[WS] Playing anim %d\n", n);
        } else {
          Serial.printf("[WS] Invalid anim %d (max: %d)\n", n, maxAnimIndex);
        }
      }
    }
    // ---- 情绪: EMO:xxx ----
    else if (s.startsWith("EMO:")) {
      String emotion = s.substring(4);
      Serial.printf("[WS] Emotion: %s\n", emotion.c_str());
      // 这里可以添加LED控制等
    }
    // ---- 表情: EXPR:xxx → 映射情绪到表情动画 ----
    // anim1=idle1, anim2=idle2, anim3=idle3, anim4=cry, anim5=angry, anim6=happy, anim7=sad, anim8=shy
    else if (s.startsWith("EXPR:")) {
      String expr = s.substring(5);
      expr.toLowerCase();
      int exprAnim = 0;
      if (expr == "happy" || expr == "excited")       exprAnim = 6;
      else if (expr == "sad")                         exprAnim = 7;
      else if (expr == "angry")                       exprAnim = 5;
      else if (expr == "fear" || expr == "cry")       exprAnim = 4;
      else if (expr == "shy" || expr == "love")       exprAnim = 8;
      else if (expr == "surprised")                   exprAnim = 6;
      else {
        // neutral/thinking/sleepy/confused/idle → 随机 idle
        exprAnim = random(1, 4); // 1~3
      }
      
      if (exprAnim >= 1 && exprAnim <= maxAnimIndex && ANIM_FRAMES[exprAnim] > 0) {
        animLoop = false;
        // 若当前正在播动画且有 frameBuffer，用渐变过渡
        if (systemMode == MODE_ANIMATION && currentAnim > 0 && frameBuffer) {
          startCrossfade(exprAnim);
          currentAnim = exprAnim;
          currentFrame = 2;  // 第 1 帧已在 crossfade 中解码
        } else {
          currentAnim = exprAnim;
          currentFrame = 1;
        }
        lastFrameTime = 0;
        systemMode = MODE_ANIMATION;
        Serial.printf("[WS] EXPR:%s -> anim%d (crossfade)\n", expr.c_str(), exprAnim);
      } else {
        Serial.printf("[WS] EXPR:%s -> anim%d (not available)\n", expr.c_str(), exprAnim);
      }
    }
    // ---- 音效播放: SND:n 或 SND:STOP ----
    else if (s.startsWith("SND:")) {
      String params = s.substring(4);
      if (params == "STOP") {
        stopLocalSound();
        Serial.println("[WS] Sound stopped");
      } else {
        int n = params.toInt();
        if (n >= 1 && n <= maxSoundIndex && SOUND_NUM_SAMPLES[n] > 0) {
          startLocalSound(n);
          Serial.printf("[WS] Playing sound s%d\n", n);
        } else {
          Serial.printf("[WS] Invalid sound %d (max: %d)\n", n, maxSoundIndex);
        }
      }
    }
    // ---- 动画+音效同步: ANIMSND:animN,sndN ----
    else if (s.startsWith("ANIMSND:")) {
      String params = s.substring(8);
      int comma = params.indexOf(',');
      if (comma > 0) {
        int animId = params.substring(0, comma).toInt();
        int sndId  = params.substring(comma + 1).toInt();
        startAnimWithSound(animId, sndId);
        Serial.printf("[WS] ANIMSND: anim%d + s%d\n", animId, sndId);
      }
    }
    // ---- 情感动作帧: EMACT:l1,l2,l3,l4,spd,acc,tail,earL,earR,dur ----
    else if (s.startsWith("EMACT:")) {
      String params = s.substring(6);
      if (params == "CLEAR") {
        if (qEmoteFrames) xQueueReset(qEmoteFrames);
        Serial.println("[EMACT] Queue cleared");
      } else {
        EmoteFrame frame;
        int parsed = sscanf(params.c_str(), "%d,%d,%d,%d,%d,%d,%d,%d,%d,%d",
                            &frame.legPos[0], &frame.legPos[1],
                            &frame.legPos[2], &frame.legPos[3],
                            &frame.legSpeed, &frame.legAccel,
                            &frame.tailAngle, &frame.earLAngle, &frame.earRAngle,
                            &frame.durationMs);
        if (parsed == 10 && qEmoteFrames) {
          // 限幅保护
          for (int i = 0; i < 4; i++) frame.legPos[i] = constrain(frame.legPos[i], 1600, 2500);
          frame.legSpeed = constrain(frame.legSpeed, 100, 2000);
          frame.legAccel = constrain(frame.legAccel, 10, 254);
          frame.tailAngle = constrain(frame.tailAngle, 0, 180);
          frame.earLAngle = constrain(frame.earLAngle, 0, 180);
          frame.earRAngle = constrain(frame.earRAngle, 0, 180);
          frame.durationMs = constrain(frame.durationMs, 50, 2000);
          
          if (xQueueSend(qEmoteFrames, &frame, 0) == pdTRUE) {
            Serial.printf("[EMACT] Queued: legs=[%d,%d,%d,%d] spd=%d tail=%d ears=[%d,%d] dur=%d\n",
                          frame.legPos[0], frame.legPos[1], frame.legPos[2], frame.legPos[3],
                          frame.legSpeed, frame.tailAngle, frame.earLAngle, frame.earRAngle,
                          frame.durationMs);
          } else {
            Serial.println("[EMACT] Queue full, frame dropped");
          }
          
          // 如果当前处于停止状态，自动切换到待机模式以执行情感动作
          if (currentGait == GAIT_STOP) {
            startGait(GAIT_IDLE);
          }
        } else if (parsed != 10) {
          Serial.printf("[EMACT] Parse error: got %d fields\n", parsed);
        }
      }
    }
    // ---- 步态控制: GAIT:xxx ----
    else if (s.startsWith("GAIT:")) {
      String gaitCmd = s.substring(5);
      gaitCmd.toUpperCase();
      Serial.printf("[WS] Gait command: %s\n", gaitCmd.c_str());
      
      if (gaitCmd == "WALK") {
        startGait(GAIT_WALK);
      } else if (gaitCmd == "TROT") {
        startGait(GAIT_TROT);
      } else if (gaitCmd == "RUN") {
        startGait(GAIT_RUN);
      } else if (gaitCmd == "IDLE") {
        startGait(GAIT_IDLE);
      } else if (gaitCmd == "SIT") {
        startGait(GAIT_SIT);
      } else if (gaitCmd == "JUMP") {
        startGait(GAIT_JUMP);
      } else if (gaitCmd == "TROT_STRAIGHT" || gaitCmd == "TS") {
        startGait(GAIT_TROT_STRAIGHT);
      } else if (gaitCmd == "BACKWARD" || gaitCmd == "BACK") {
        startGait(GAIT_BACKWARD);
      } else if (gaitCmd == "EFFICIENT_WALK" || gaitCmd == "EFF" || gaitCmd == "EFFWALK") {
        startGait(GAIT_EFFICIENT_WALK);
      } else if (gaitCmd == "WAVE") {
        startGait(GAIT_WAVE);
      } else if (gaitCmd == "LAYDOWN" || gaitCmd == "LAY") {
        startGait(GAIT_LAYDOWN);
      } else if (gaitCmd == "NEWYEAR" || gaitCmd == "NY") {
        startGait(GAIT_NEWYEAR);
      } else if (gaitCmd == "STUMBLE" || gaitCmd == "FALL") {
        startGait(GAIT_STUMBLE);
      } else if (gaitCmd == "STOP") {
        stopGait();
      } else if (gaitCmd == "LEFT") {
        // 左转
        turnFactor = -1.0f;
        Serial.println("[GAIT] Turn LEFT");
      } else if (gaitCmd == "RIGHT") {
        // 右转
        turnFactor = 1.0f;
        Serial.println("[GAIT] Turn RIGHT");
      } else if (gaitCmd == "STRAIGHT") {
        // 直走
        turnFactor = 0.0f;
        Serial.println("[GAIT] Go STRAIGHT");
      } else if (gaitCmd.startsWith("TURN:")) {
        // 精确转向: GAIT:TURN:-0.5 到 GAIT:TURN:0.5
        String turnVal = gaitCmd.substring(5);
        turnFactor = constrain(turnVal.toFloat(), -1.0f, 1.0f);
        Serial.printf("[GAIT] Turn factor: %.2f\n", turnFactor);
      } else if (gaitCmd == "CENTER") {
        // 复位到中心
        stopGait();
        resetLegsToCenter();
        Serial.println("[GAIT] Reset to center");
      }
    }
  });
}

// ====================================================================
// 开机加载动画 - 省略号逐渐显示
// ====================================================================
int loadingDotCount = 0;

void displayLoadingDots(int dots) {
  // 在屏幕中央显示逐渐增加的省略号
  tft.fillScreen(ST77XX_BLACK);
  
  // 计算居中位置 (170x320 屏幕)
  int textSize = 4;
  int dotWidth = 6 * textSize;  // 每个字符约6像素宽 * textSize
  int totalWidth = dots * dotWidth;
  int startX = (TFT_W - totalWidth) / 2;
  int startY = (TFT_H / 2) - (textSize * 4);  // 垂直居中
  
  tft.setTextSize(textSize);
  tft.setTextColor(ST77XX_WHITE);
  tft.setCursor(startX, startY);
  
  for (int i = 0; i < dots; i++) {
    tft.print(".");
  }
}

void advanceLoadingDots() {
  loadingDotCount++;
  if (loadingDotCount > 6) loadingDotCount = 1;
  displayLoadingDots(loadingDotCount);
}

// ====================================================================
// WiFi 连接成功提示音 - 空灵双音符
// ====================================================================
void playConnectedTone() {
  Serial.println("[TONE] Playing connected chime...");
  
  const int sampleRate = 16000;
  const float volume = 0.25f;  // 音量（不要太大）
  
  // 两个空灵的低沉音符
  // 第一个音: G3 (196 Hz) - 持续 350ms，带淡入淡出
  // 第二个音: C4 (262 Hz) - 持续 450ms，带淡入淡出
  // 中间间隔 80ms
  
  struct ToneNote {
    float freq;
    int durationMs;
  };
  
  ToneNote notes[] = {
    {196.0f, 350},   // G3 - 空灵低音
    {262.0f, 450},   // C4 - 稍高一点，形成和谐五度
  };
  int gapMs = 80;
  
  // 临时缓冲区
  const int chunkSamples = 128;
  int32_t outBuf[chunkSamples * 2];  // 立体声
  
  for (int n = 0; n < 2; n++) {
    float freq = notes[n].freq;
    int totalSamples = (sampleRate * notes[n].durationMs) / 1000;
    int fadeInSamples = sampleRate / 20;   // 50ms 淡入
    int fadeOutSamples = sampleRate / 8;   // 125ms 淡出
    
    int samplesWritten = 0;
    while (samplesWritten < totalSamples) {
      int remaining = totalSamples - samplesWritten;
      int chunk = (remaining < chunkSamples) ? remaining : chunkSamples;
      
      for (int i = 0; i < chunk; i++) {
        int idx = samplesWritten + i;
        float t = (float)idx / sampleRate;
        
        // 基波 + 微弱的泛音，产生空灵感
        float sample = sinf(2.0f * M_PI * freq * t) * 0.7f
                     + sinf(2.0f * M_PI * freq * 2.0f * t) * 0.15f
                     + sinf(2.0f * M_PI * freq * 3.0f * t) * 0.08f;
        
        // 淡入淡出包络
        float envelope = 1.0f;
        if (idx < fadeInSamples) {
          envelope = (float)idx / fadeInSamples;
        } else if (idx > totalSamples - fadeOutSamples) {
          envelope = (float)(totalSamples - idx) / fadeOutSamples;
        }
        
        sample *= envelope * volume;
        
        // 限幅
        if (sample > 1.0f) sample = 1.0f;
        if (sample < -1.0f) sample = -1.0f;
        
        int16_t s16 = (int16_t)(sample * 32000);
        int32_t v32 = (int32_t)s16 << 16;
        outBuf[i * 2 + 0] = v32;  // 左声道
        outBuf[i * 2 + 1] = v32;  // 右声道
      }
      
      size_t bytes = chunk * 2 * sizeof(int32_t);
      size_t off = 0;
      while (off < bytes) {
        size_t wrote = i2sOut.write((uint8_t*)outBuf + off, bytes - off);
        if (wrote == 0) delay(1);
        else off += wrote;
      }
      
      samplesWritten += chunk;
    }
    
    // 两个音之间的间隔 - 写入静音
    if (n == 0) {
      int gapSamples = (sampleRate * gapMs) / 1000;
      memset(outBuf, 0, sizeof(outBuf));
      int gapWritten = 0;
      while (gapWritten < gapSamples) {
        int chunk = ((gapSamples - gapWritten) < chunkSamples) ? (gapSamples - gapWritten) : chunkSamples;
        size_t bytes = chunk * 2 * sizeof(int32_t);
        size_t off = 0;
        while (off < bytes) {
          size_t wrote = i2sOut.write((uint8_t*)outBuf + off, bytes - off);
          if (wrote == 0) delay(1);
          else off += wrote;
        }
        gapWritten += chunk;
      }
    }
  }
  
  Serial.println("[TONE] Chime done");
}

// ====================================================================
// Setup
// ====================================================================
void setup() {
  Serial.begin(115200);
  delay(500);
  
  Serial.println("\n========================================");
  Serial.println("  ESP32S3 Integrated System");
  Serial.println("========================================");
  
  // 检查 PSRAM
  if (psramFound()) {
    Serial.printf("PSRAM: %d MB\n", ESP.getPsramSize() / 1024 / 1024);
    jpegBuffer = (uint8_t *)ps_malloc(JPEG_BUFFER_SIZE);
    frameBuffer = (uint16_t *)ps_malloc(FRAME_BUFFER_SIZE);
    remoteScreenBuffer = (uint16_t *)ps_malloc(FRAME_BUFFER_SIZE);
  } else {
    Serial.println("PSRAM: Not found");
    jpegBuffer = (uint8_t *)malloc(JPEG_BUFFER_SIZE);
    frameBuffer = NULL;
    remoteScreenBuffer = (uint16_t *)malloc(FRAME_BUFFER_SIZE);
  }
  
  // 初始化屏幕
  Serial.println("Init TFT...");
  SPI.begin(TFT_SCK, -1, TFT_MOSI, TFT_CS);
  tft.init(TFT_W, TFT_H);
  tft.setRotation(2);  // 旋转180度 (0=0度, 1=90度, 2=180度, 3=270度)
  tft.setSPISpeed(80000000);
  tft.fillScreen(ST77XX_BLACK);
  if (remoteScreenBuffer != NULL) {
    memset(remoteScreenBuffer, 0xFF, FRAME_BUFFER_SIZE);
  }
  setScreenPageMode(SCREEN_PAGE_EXPRESSION);
  
  // 显示加载动画（省略号）
  loadingDotCount = 1;
  displayLoadingDots(loadingDotCount);
  
  // 初始化 LittleFS 并自动扫描所有动画文件夹
  if (LittleFS.begin(true)) {
    Serial.println("LittleFS OK");
    Serial.println("Scanning animation folders...");
    
    // 初始化数组
    for (int i = 0; i <= MAX_ANIMS; i++) {
      ANIM_FRAMES[i] = 0;
      ANIM_PATHS[i][0] = '\0';
    }
    
    // 自动扫描 anim1 到 anim20
    maxAnimIndex = 0;
    for (int i = 1; i <= MAX_ANIMS; i++) {
      char animPath[16];
      sprintf(animPath, "/anim%d", i);
      
      if (LittleFS.exists(animPath)) {
        strcpy(ANIM_PATHS[i], animPath);
        ANIM_FRAMES[i] = countAnimFrames(animPath);
        if (ANIM_FRAMES[i] > 0) {
          maxAnimIndex = i;  // 更新最大动画编号
          Serial.printf("  Found %s: %d frames\n", animPath, ANIM_FRAMES[i]);
        }
      }
    }
    
    if (maxAnimIndex > 0) {
      Serial.printf("Total animations found: %d (anim1 to anim%d)\n", maxAnimIndex, maxAnimIndex);
    } else {
      Serial.println("  No animations found");
    }
    
    // 扫描 /sounds/ 目录
    maxSoundIndex = 0;
    for (int i = 1; i <= MAX_SOUNDS; i++) {
      char sndPath[24];
      sprintf(sndPath, "/sounds/s%d.adp", i);
      if (LittleFS.exists(sndPath)) {
        strcpy(SOUND_PATHS[i], sndPath);
        File sf = LittleFS.open(sndPath, "r");
        if (sf) {
          AdpFileHeader hdr;
          if (sf.read((uint8_t*)&hdr, sizeof(hdr)) == sizeof(hdr) &&
              memcmp(hdr.magic, "ADPM", 4) == 0) {
            SOUND_NUM_SAMPLES[i] = hdr.numSamples;
            SOUND_SAMPLE_RATES[i] = hdr.sampleRate;
            maxSoundIndex = i;
            Serial.printf("  Found %s: %.1fs (%uHz)\n", sndPath,
                          (float)hdr.numSamples / hdr.sampleRate, hdr.sampleRate);
          }
          sf.close();
        }
      }
    }
    if (maxSoundIndex > 0) {
      Serial.printf("Total sounds found: %d\n", maxSoundIndex);
    }
  }
  
  // 初始化 I2C 和 PCA9685
  Serial.println("Init I2C + PCA9685...");
  Wire.begin(I2C_SDA, I2C_SCL);
  pca.begin();
  pca.setPWMFreq(SERVO_FREQ);
  for (int i = 0; i < 8; i++) {
    setServoAngle(i, 90);
  }
  // 初始化嘴巴、耳朵和尾巴舵机: CH12=嘴巴, CH13=尾巴, CH14=左耳, CH15=右耳
  setServoAngle(12, MOUTH_CLOSED_ANGLE);  // 嘴巴初始化为闭合状态
  setServoAngle(13, 90);  // 尾巴
  setServoAngle(14, 90);  // 左耳
  setServoAngle(15, 90);  // 右耳
  Serial.println("  Mouth, Ears & Tail servos initialized (CH12-15)");
  advanceLoadingDots();  // 2个点
  
  // 初始化 STS3032
  Serial.println("Init STS3032...");
  Serial1.begin(STS_BAUD, SERIAL_8N1, STS_RX, STS_TX);
  delay(100);
  sts.pSerial = &Serial1;
  advanceLoadingDots();  // 3个点
  
  // 初始化摄像头
  Serial.println("Init Camera...");
  if (!init_camera()) {
    Serial.println("Camera init failed!");
  } else {
    Serial.println("Camera OK");
  }
  advanceLoadingDots();  // 4个点
  
  // 初始化 I2S（麦克风和扬声器现在使用不同引脚，可以同时工作）
  Serial.println("Init I2S...");
  init_i2s_in();   // 麦克风: GPIO42, GPIO41
  init_i2s_out();  // 扬声器: D3(GPIO4), D2(GPIO3), D9(GPIO8)
  advanceLoadingDots();  // 5个点
  
  // 初始化队列
  qFrames = xQueueCreate(3, sizeof(fb_ptr_t));
  qAudio  = xQueueCreate(AUDIO_QUEUE_DEPTH, sizeof(AudioChunk));
  qEmoteFrames = xQueueCreate(EMOTE_QUEUE_SIZE, sizeof(EmoteFrame));
  
  // 启动任务
  xTaskCreatePinnedToCore(taskCamCapture, "cam_cap", 10240, NULL, 4, NULL, 1);
  xTaskCreatePinnedToCore(taskCamSend,    "cam_snd",  8192, NULL, 3, NULL, 1);
  xTaskCreatePinnedToCore(taskMicCapture, "mic_cap",  4096, NULL, 2, NULL, 0);
  xTaskCreatePinnedToCore(taskMicUpload,  "mic_upl",  4096, NULL, 2, NULL, 1);
  xTaskCreatePinnedToCore(taskHttpPlay,   "tts_play", 8192, NULL, 3, &taskHttpPlayHandle, 0);
  xTaskCreatePinnedToCore(taskMouthDriver, "mouth_drv", 2048, NULL, 1, &mouthTaskHandle, 1);
  xTaskCreatePinnedToCore(taskLocalSound,  "snd_play", 6144, NULL, 2, &taskLocalSoundHandle, 0);
  
  // WiFi 连接
  Serial.println("Connecting WiFi...");
  advanceLoadingDots();  // 6个点
  
  WiFi.mode(WIFI_STA);
  WiFi.setSleep(false);
  esp_wifi_set_ps(WIFI_PS_NONE);
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  
  uint32_t wifiStart = millis();
  int wifiDotTimer = 0;
  while (WiFi.status() != WL_CONNECTED && millis() - wifiStart < 10000) {
    delay(300);
    Serial.print(".");
    wifiDotTimer++;
    if (wifiDotTimer % 2 == 0) {
      advanceLoadingDots();  // WiFi等待期间持续动画
    }
  }
  
  if (WiFi.status() == WL_CONNECTED) {
    Serial.println("\nWiFi Connected: " + WiFi.localIP().toString());
    statusLine1 = "WiFi: " + WiFi.localIP().toString();
    systemMode = MODE_ONLINE;
    
    // WiFi 连接成功 - 播放空灵提示音
    playConnectedTone();
  } else {
    Serial.println("\nWiFi Failed - still showing animations");
    statusLine1 = "WiFi: Offline";
  }
  // 无论 WiFi 是否连接，都按几率显示动画，不显示 OFFLINE
  switchToIdleOrOffline();
  
  // HTTP 服务器
  server.on("/", handleRoot);
  server.on("/servo", handleServo);
  server.on("/sts", handleSTS);
  server.on("/anim", handleAnim);
  server.on("/status", handleStatus);
  server.on("/animlist", handleAnimList);
  server.on("/soundlist", handleSoundList);
  server.on("/gait", handleGait);  // 步态控制
  server.begin();
  Serial.println("HTTP Server started");
  
  // WebSocket 回调
  setupWebSocketCallbacks();
  
  // 默认按几率启动动画循环（switchToIdleOrOffline 已在上面调用，若需覆盖为从 anim1 起播可取消下面注释）
  // animLoop = true;
  // for (int i = 1; i <= maxAnimIndex; i++) {
  //   if (ANIM_FRAMES[i] > 0) { currentAnim = i; currentFrame = 1; lastFrameTime = 0; systemMode = MODE_ANIMATION; break; }
  // }
  
  Serial.println("========================================");
  Serial.println("  System Ready!");
  Serial.println("========================================");
}

// ====================================================================
// Loop
// ====================================================================
uint32_t lastCamRetryMs = 0;
uint32_t lastAudRetryMs = 0;
uint32_t lastStatusUpdateMs = 0;

void loop() {
  uint32_t now = millis();
  
  // HTTP 请求处理
  server.handleClient();
  
  // 渐变过渡：优先驱动 crossfade（每帧 ~20ms 刷新一次）
  if (screenPageMode == SCREEN_PAGE_EXPRESSION && crossfadeActive) {
    updateCrossfade();
  }
  
  // 动画播放（渐变期间暂停正常帧推进，等渐变结束再继续）
  if (screenPageMode == SCREEN_PAGE_EXPRESSION && systemMode == MODE_ANIMATION && currentAnim > 0 && !crossfadeActive) {
    int effectiveDelay = (animsnd_frame_delay > 0) ? animsnd_frame_delay : FRAME_DELAY;
    if (lastFrameTime == 0 || (now - lastFrameTime >= (unsigned long)effectiveDelay)) {
      playAnimationFrame();
      lastFrameTime = millis();
    }
  }
  renderRemoteScreenIfNeeded();
  
  // WebSocket 连接维护
  if (WiFi.status() == WL_CONNECTED) {
    // Camera WebSocket
    if (!wsCam.available()) {
      if (now - lastCamRetryMs >= 2000) {
        lastCamRetryMs = now;
        if (wsCam.connect(SERVER_HOST, SERVER_PORT, CAM_WS_PATH)) {
          Serial.println("[WS-CAM] Connected");
        }
      }
    }
    
    // Audio WebSocket
    if (!wsAud.available()) {
      if (now - lastAudRetryMs >= 3000) {
        lastAudRetryMs = now;
        if (wsAud.connect(SERVER_HOST, SERVER_PORT, AUD_WS_PATH)) {
          Serial.println("[WS-AUD] Connected");
          aud_ws_ready = true;
          delay(100);  // 等待连接稳定
          run_audio_stream = true;
          mic_enabled = true;
          wsAud.send("START");
          Serial.println("[WS-AUD] Sent START command");
        }
      }
    }
    
    wsCam.poll();
    wsAud.poll();
  }
  
  // 若非动画模式则切回按几率的动画循环，绝不刷新出 OFFLINE 画面
  if (screenPageMode == SCREEN_PAGE_EXPRESSION && now - lastStatusUpdateMs >= 30000 && systemMode != MODE_ANIMATION && !cam_ws_ready) {
    lastStatusUpdateMs = now;
    switchToIdleOrOffline();
  }
  
  // 串口命令处理
  if (Serial.available()) {
    char cmd = Serial.read();
    if (cmd == 'h' || cmd == 'H' || cmd == '?') {
      Serial.println("\n=== Commands ===");
      Serial.println("1-5: Test modes");
      Serial.println("a1/a2/a3: Play animation");
      Serial.println("a0: Stop animation");
      Serial.println("s: Scan STS3032");
    } else if (cmd == 's' || cmd == 'S') {
      Serial.println("\nScanning STS3032...");
      for (int id = 1; id <= 20; id++) {
        if (sts.Ping(id) != -1) {
          Serial.printf("  Found ID: %d\n", id);
        }
      }
    } else if (cmd == 'a' || cmd == 'A') {
      delay(50);
      if (Serial.available()) {
        char subCmd = Serial.read();
        // 支持单数字（1-9）和两位数（10-20）
        int n = 0;
        if (subCmd >= '1' && subCmd <= '9') {
          n = subCmd - '0';
          // 检查是否有两位数
          if (Serial.available()) {
            char nextChar = Serial.peek();
            if (nextChar >= '0' && nextChar <= '9') {
              n = n * 10 + (Serial.read() - '0');
            }
          }
        }
        
        if (n >= 1 && n <= maxAnimIndex && ANIM_FRAMES[n] > 0) {
          currentAnim = n;
          currentFrame = 1;
          lastFrameTime = 0;
          systemMode = MODE_ANIMATION;
          Serial.printf("Playing anim %d\n", n);
        } else if (subCmd == '0') {
          currentAnim = 0;
          animLoop = false;
          switchToIdleOrOffline();
          Serial.println("Animation stopped");
        } else if (n > 0) {
          Serial.printf("Invalid anim %d (max: %d)\n", n, maxAnimIndex);
        }
      }
    }
  }
  
  delay(2);
}

// ====================================================================
// 步态控制实现
// ====================================================================

int calcSpeed(int displacement, float timeSec) {
  // STS舵机speed参数单位是步/秒
  if (timeSec <= 0) return 0;  // 0 = 最大速度
  return (int)(abs(displacement) / timeSec);
}

void startGait(GaitMode mode) {
  if (mode == currentGait && gaitRunning) return;
  
  // 清空情感动作队列（切换步态时丢弃旧的情感动作）
  if (qEmoteFrames) xQueueReset(qEmoteFrames);
  
  // 先停止当前步态
  stopGait();
  delay(50);
  
  targetGait = mode;
  currentGait = mode;
  gaitRunning = true;
  
  // 创建步态控制任务
  xTaskCreatePinnedToCore(
    taskGaitControl,
    "gait_ctrl",
    8192,
    NULL,
    2,
    &gaitTaskHandle,
    0
  );
  
  Serial.printf("[GAIT] Started mode: %d\n", mode);
}

void stopGait() {
  gaitRunning = false;
  targetGait = GAIT_STOP;
  
  // 等待任务结束
  if (gaitTaskHandle != nullptr) {
    vTaskDelay(pdMS_TO_TICKS(100));
    gaitTaskHandle = nullptr;
  }
  
  currentGait = GAIT_STOP;
  Serial.println("[GAIT] Stopped");
}

void resetLegsToCenter() {
  for (int id = 1; id <= 4; id++) {
    stsMove(id, 2048, 1200, 180);
  }
  delay(200);
}

void taskGaitControl(void* pvParams) {
  Serial.printf("[GAIT] Task started, mode=%d\n", currentGait);
  
  while (gaitRunning) {
    switch (currentGait) {
      case GAIT_WALK:
        runWalkCycle();
        break;
      case GAIT_TROT:
        runTrotCycle();
        break;
      case GAIT_RUN:
        runRunCycle();
        break;
      case GAIT_IDLE:
        runIdleLoop();
        break;
      case GAIT_SIT:
        runSitAction();
        break;
      case GAIT_JUMP:
        runJumpAction();
        break;
      case GAIT_TROT_STRAIGHT:
        runTrotStraightCycle();
        break;
      case GAIT_BACKWARD:
        runBackwardCycle();
        break;
      case GAIT_EFFICIENT_WALK:
        runEfficiencyWalkCycle();
        break;
      case GAIT_WAVE:
        runWaveCycle();
        break;
      case GAIT_LAYDOWN:
        runLaydownAction();
        break;
      case GAIT_NEWYEAR:
        runNewYearAction();
        break;
      case GAIT_STUMBLE:
        runStumbleAction();
        break;
      default:
        vTaskDelay(pdMS_TO_TICKS(100));
        break;
    }
  }
  
  // 退出前复位（腿+耳朵+尾巴）
  resetLegsToCenter();
  setServoAngle(13, 90);  // 尾巴回中
  delay(10);
  setServoAngle(14, 90);  // 左耳回中
  delay(10);
  setServoAngle(15, 90);  // 右耳回中
  Serial.println("[GAIT] Task ended");
  vTaskDelete(NULL);
}

// ============ 慢走步态 ============
void runWalkCycle() {
  // 尾巴点到点振荡变量
  bool tailLeft_w = false;
  uint32_t nextTailSwitch_w = millis();
  
  // 初始化：所有腿回到ready位置
  for (int i = 0; i < 4; i++) {
    int id = i + 1;
    int readyPos = WALK_CONFIG[i].ready;
    
    // 转向调整：左转时右腿幅度大，右转时左腿幅度大
    if (turnFactor != 0) {
      float adjust = turnFactor * turnStrength * 100;
      if (id == 1 || id == 3) {  // 左腿
        readyPos += (int)adjust;  // 右转时左腿幅度增大
      } else {  // 右腿
        readyPos -= (int)adjust;  // 左转时右腿幅度增大
      }
    }
    stsMove(id, readyPos, 800, 150);
  }
  vTaskDelay(pdMS_TO_TICKS(300));
  
  // 记录每腿的当前阶段和开始时间
  int legPhase[4] = {0, 0, 0, 0};  // 0-3: 四个阶段
  uint32_t legStartTime[4];
  uint32_t now = millis();
  
  // 根据相位初始化开始时间
  for (int i = 0; i < 4; i++) {
    int phaseOffset = WALK_CONFIG[i].phase;
    legStartTime[i] = now - (uint32_t)(phaseOffset * WALK_PHASE_DELAY * 1000);
  }
  
  // 主循环
  while (gaitRunning && currentGait == GAIT_WALK) {
    now = millis();
    
    for (int i = 0; i < 4; i++) {
      int id = i + 1;
      const LegConfig& cfg = WALK_CONFIG[i];
      
      // 计算当前腿的周期时间
      float t = (now - legStartTime[i]) / 1000.0f;
      t = fmod(t, WALK_CYCLE_TOTAL);
      
      // 转向调整
      float turnAdjust = 0;
      if (turnFactor != 0) {
        if (id == 1 || id == 3) {  // 左腿
          turnAdjust = turnFactor * turnStrength * 150;
        } else {  // 右腿
          turnAdjust = -turnFactor * turnStrength * 150;
        }
      }
      
      // 确定当前阶段并发送命令
      float t1 = WALK_PHASE_1;
      float t2 = t1 + WALK_PHASE_2;
      float t3 = t2 + WALK_PHASE_3;
      
      int targetPos, speed, accel;
      
      if (t < t1) {
        // 阶段1: ready→back 蓄力
        targetPos = cfg.back + (int)turnAdjust;
        speed = calcSpeed(abs(cfg.back - cfg.ready), WALK_PHASE_1);
        accel = WALK_ACCEL_FAST;
      } else if (t < t2) {
        // 阶段2: back→front 发力推出
        targetPos = cfg.front + (int)turnAdjust;
        speed = calcSpeed(abs(cfg.front - cfg.back), WALK_PHASE_2);
        accel = WALK_ACCEL_FAST;
      } else if (t < t3) {
        // 阶段3: front→neutral 过渡减速
        targetPos = cfg.neutral + (int)turnAdjust;
        speed = calcSpeed(abs(cfg.neutral - cfg.front), WALK_PHASE_3);
        accel = WALK_ACCEL_TRANS;  // 过渡加速度
      } else {
        // 阶段4: neutral→ready 缓缓收回
        targetPos = cfg.ready + (int)turnAdjust;
        speed = calcSpeed(abs(cfg.ready - cfg.neutral), WALK_PHASE_4);
        accel = WALK_ACCEL_SLOW;
      }
      
      // 只在阶段切换时发送命令
      int newPhase = (t < t1) ? 0 : (t < t2) ? 1 : (t < t3) ? 2 : 3;
      if (newPhase != legPhase[i]) {
        legPhase[i] = newPhase;
        stsMove(id, targetPos, speed, accel);
      }
    }
    
    // 尾巴：轻柔摆动，低频率
    if (now >= nextTailSwitch_w) {
      tailLeft_w = !tailLeft_w;
      setServoAngle(13, tailLeft_w ? 78 : 102);  // ±12° 轻柔摆动
      nextTailSwitch_w = now + random(3000, 5000);  // 3~5秒一次
    }
    
    vTaskDelay(pdMS_TO_TICKS(10));
  }
  
  // 慢走结束，尾巴回中立
  setServoAngle(13, 90);
}

// ============ 快走（对角）步态 ============
void runTrotCycle() {
  // 初始化
  for (int i = 0; i < 4; i++) {
    stsMove(i + 1, TROT_CONFIG[i].ready, 800, 150);
  }
  vTaskDelay(pdMS_TO_TICKS(200));
  
  int legPhase[4] = {0, 0, 0, 0};
  uint32_t now = millis();
  uint32_t legStartTime[4];
  bool tailLeft_t = false;
  uint32_t nextTailSwitch_t = now;
  
  // 对角步态：ID1+ID4同步，ID2+ID3同步
  for (int i = 0; i < 4; i++) {
    int phaseOffset = TROT_CONFIG[i].phase;  // 0 或 1
    legStartTime[i] = now - (uint32_t)(phaseOffset * TROT_PHASE_DELAY * 1000);
  }
  
  while (gaitRunning && currentGait == GAIT_TROT) {
    now = millis();
    
    for (int i = 0; i < 4; i++) {
      int id = i + 1;
      const LegConfig& cfg = TROT_CONFIG[i];
      
      float t = (now - legStartTime[i]) / 1000.0f;
      t = fmod(t, TROT_CYCLE_TOTAL);
      
      // 转向调整
      float turnAdjust = 0;
      if (turnFactor != 0) {
        if (id == 1 || id == 3) {
          turnAdjust = turnFactor * turnStrength * 200;
        } else {
          turnAdjust = -turnFactor * turnStrength * 200;
        }
      }
      
      float t1 = TROT_PHASE_1;
      float t2 = t1 + TROT_PHASE_2;
      float t3 = t2 + TROT_PHASE_3;
      
      int targetPos, speed, accel;
      
      if (t < t1) {
        // 阶段1: ready→back 蓄力
        targetPos = cfg.back + (int)turnAdjust;
        speed = calcSpeed(abs(cfg.back - cfg.ready), TROT_PHASE_1);
        accel = TROT_ACCEL_FAST;
      } else if (t < t2) {
        // 阶段2: back→front 发力推出
        targetPos = cfg.front + (int)turnAdjust;
        speed = calcSpeed(abs(cfg.front - cfg.back), TROT_PHASE_2);
        accel = TROT_ACCEL_FAST;
      } else if (t < t3) {
        // 阶段3: front→neutral 过渡减速
        targetPos = cfg.neutral + (int)turnAdjust;
        speed = calcSpeed(abs(cfg.neutral - cfg.front), TROT_PHASE_3);
        accel = TROT_ACCEL_TRANS;  // 过渡加速度
      } else {
        // 阶段4: neutral→ready 收回
        targetPos = cfg.ready + (int)turnAdjust;
        speed = calcSpeed(abs(cfg.ready - cfg.neutral), TROT_PHASE_4);
        accel = TROT_ACCEL_TRANS;  // 过渡加速度
      }
      
      int newPhase = (t < t1) ? 0 : (t < t2) ? 1 : (t < t3) ? 2 : 3;
      if (newPhase != legPhase[i]) {
        legPhase[i] = newPhase;
        stsMove(id, targetPos, speed, accel);
      }
    }
    
    // 尾巴：中等幅度摆动，低频率
    if (now >= nextTailSwitch_t) {
      tailLeft_t = !tailLeft_t;
      setServoAngle(13, tailLeft_t ? 70 : 110);  // ±20° 中等摆动
      nextTailSwitch_t = now + random(2500, 4500);  // 2.5~4.5秒一次
    }
    
    vTaskDelay(pdMS_TO_TICKS(8));
  }
  
  // 快走结束，尾巴回中立
  setServoAngle(13, 90);
}

// ============ 跑步步态 ============
void runRunCycle() {
  // 耳朵缓慢转到竖起位置（跑步警觉状态）
  setServoAngle(14, 10);   // 左耳竖起
  delay(10);               // I2C间隔，确保两个命令都送达
  setServoAngle(15, 170);  // 右耳竖起
  
  // 初始化
  for (int i = 0; i < 4; i++) {
    stsMove(i + 1, RUN_CONFIG[i].ready, 800, 150);
  }
  vTaskDelay(pdMS_TO_TICKS(150));
  
  int legPhase[4] = {0, 0, 0, 0};
  uint32_t now = millis();
  uint32_t legStartTime[4];
  bool tailLeft_r = false;
  uint32_t nextTailSwitch_r = now;
  
  // 顺序步态：左后→右后→左前→右前
  for (int i = 0; i < 4; i++) {
    int phaseOffset = RUN_CONFIG[i].phase;  // 0, 1, 2, 3
    legStartTime[i] = now - (uint32_t)(phaseOffset * RUN_PHASE_DELAY * 1000);
  }
  
  while (gaitRunning && currentGait == GAIT_RUN) {
    now = millis();
    
    for (int i = 0; i < 4; i++) {
      int id = i + 1;
      const LegConfig& cfg = RUN_CONFIG[i];
      
      float t = (now - legStartTime[i]) / 1000.0f;
      t = fmod(t, RUN_CYCLE_TOTAL);
      
      // 转向调整
      float turnAdjust = 0;
      if (turnFactor != 0) {
        if (id == 1 || id == 3) {
          turnAdjust = turnFactor * turnStrength * 250;
        } else {
          turnAdjust = -turnFactor * turnStrength * 250;
        }
      }
      
      float t1 = RUN_PHASE_1;
      float t2 = t1 + RUN_PHASE_2;
      float t3 = t2 + RUN_PHASE_3;
      
      int targetPos, speed, accel;
      
      if (t < t1) {
        targetPos = cfg.back + (int)turnAdjust;
        speed = calcSpeed(abs(cfg.back - cfg.ready), RUN_PHASE_1);
        accel = RUN_ACCEL_FAST;
      } else if (t < t2) {
        targetPos = cfg.front + (int)turnAdjust;
        speed = calcSpeed(abs(cfg.front - cfg.back), RUN_PHASE_2);
        accel = RUN_ACCEL_FAST;
      } else if (t < t3) {
        targetPos = cfg.neutral + (int)turnAdjust;
        speed = calcSpeed(abs(cfg.neutral - cfg.front), RUN_PHASE_3);
        accel = RUN_ACCEL_FAST;
      } else {
        targetPos = cfg.ready + (int)turnAdjust;
        speed = calcSpeed(abs(cfg.ready - cfg.neutral), RUN_PHASE_4);
        accel = RUN_ACCEL_SLOW;
      }
      
      int newPhase = (t < t1) ? 0 : (t < t2) ? 1 : (t < t3) ? 2 : 3;
      if (newPhase != legPhase[i]) {
        legPhase[i] = newPhase;
        stsMove(id, targetPos, speed, accel);
      }
    }
    
    // 尾巴：大幅度摆动，低频率
    if (now >= nextTailSwitch_r) {
      tailLeft_r = !tailLeft_r;
      setServoAngle(13, tailLeft_r ? 45 : 135);  // ±45° 大幅摆动
      nextTailSwitch_r = now + random(2000, 4000);  // 2~4秒一次
    }
    
    vTaskDelay(pdMS_TO_TICKS(5));  // 更快的更新频率
  }
  
  // 跑步结束，耳朵和尾巴缓缓回中立
  setServoAngle(13, 90);  // 尾巴回中
  delay(10);
  setServoAngle(14, 90);  // 左耳回中
  delay(10);
  setServoAngle(15, 90);  // 右耳回中
}

// ============ 待机步态 ============
void runIdleLoop() {
  resetLegsToCenter();
  
  // 状态记录
  int currentPos[4] = {2048, 2048, 2048, 2048};
  uint32_t nextSwayTime[4];
  uint32_t now = millis();
  
  for (int i = 0; i < 4; i++) {
    nextSwayTime[i] = now + random(800, 2500);
  }
  
  // 耳朵和尾巴的随机动作计时器（不对称，各自独立）
  uint32_t nextTailTime = now + random(2000, 5000);
  uint32_t nextLeftEarTime = now + random(3000, 7000);
  uint32_t nextRightEarTime = now + random(4000, 8000);
  bool tailAtNeutral = true;
  bool leftEarAtNeutral = true;
  bool rightEarAtNeutral = true;
  
  while (gaitRunning && currentGait == GAIT_IDLE) {
    now = millis();
    
    // ---- 情感动作帧处理（优先级高于普通待机行为）----
    EmoteFrame emoteFrame;
    if (qEmoteFrames && xQueueReceive(qEmoteFrames, &emoteFrame, 0) == pdTRUE) {
      // 执行情感动作：移动四条腿
      for (int i = 0; i < 4; i++) {
        stsMove(i + 1, emoteFrame.legPos[i], emoteFrame.legSpeed, emoteFrame.legAccel);
      }
      // 设置尾巴和耳朵
      setServoAngle(13, emoteFrame.tailAngle);
      delay(5);
      setServoAngle(14, emoteFrame.earLAngle);
      delay(5);
      setServoAngle(15, emoteFrame.earRAngle);
      
      Serial.printf("[EMACT] Exec: legs=[%d,%d,%d,%d] tail=%d ears=[%d,%d] dur=%d\n",
                    emoteFrame.legPos[0], emoteFrame.legPos[1],
                    emoteFrame.legPos[2], emoteFrame.legPos[3],
                    emoteFrame.tailAngle, emoteFrame.earLAngle, emoteFrame.earRAngle,
                    emoteFrame.durationMs);
      
      // 保持姿势指定时间（同时检查退出条件）
      uint32_t holdEnd = millis() + emoteFrame.durationMs;
      while (millis() < holdEnd && gaitRunning && currentGait == GAIT_IDLE) {
        vTaskDelay(pdMS_TO_TICKS(20));
      }
      
      // 更新位置追踪和计时器（避免情感动作后立即触发普通动作）
      for (int i = 0; i < 4; i++) {
        currentPos[i] = emoteFrame.legPos[i];
        nextSwayTime[i] = millis() + random(800, 2000);
      }
      nextTailTime = millis() + random(2000, 4000);
      nextLeftEarTime = millis() + random(2000, 5000);
      nextRightEarTime = millis() + random(2000, 5000);
      tailAtNeutral = (emoteFrame.tailAngle >= 85 && emoteFrame.tailAngle <= 95);
      leftEarAtNeutral = (emoteFrame.earLAngle >= 85 && emoteFrame.earLAngle <= 95);
      rightEarAtNeutral = (emoteFrame.earRAngle >= 85 && emoteFrame.earRAngle <= 95);
      
      continue;  // 跳过本轮普通待机逻辑
    }
    
    for (int i = 0; i < 4; i++) {
      if (now >= nextSwayTime[i]) {
        int id = i + 1;
        const IdleConfig& cfg = IDLE_CONFIG[i];
        
        // 随机微晃
        int swayOffset = random(-cfg.swayRange, cfg.swayRange + 1);
        int targetPos = cfg.base + swayOffset;
        
        stsMove(id, targetPos, random(150, 400), random(30, 80));
        currentPos[i] = targetPos;
        
        // 设置下次晃动时间
        nextSwayTime[i] = now + random(800, 2500);
        
        // 8%概率踢腿
        if (random(100) < 8) {
          vTaskDelay(pdMS_TO_TICKS(300));
          // 踢腿：前腿向前抬(ID1=+,ID2=-), 后腿向后抬(ID3=-,ID4=+)
          int kickDir = (id == 1 || id == 4) ? 1 : -1;
          int kickPos = cfg.base + kickDir * cfg.kickRange;
          stsMove(id, kickPos, 0, 254);  // 最快速度
          vTaskDelay(pdMS_TO_TICKS(150));
          stsMove(id, cfg.base, 300, 50);  // 缓慢收回
          vTaskDelay(pdMS_TO_TICKS(200));
        }
      }
    }
    
    // 尾巴：随机不对称摆动（20%概率，仅在需要时发命令）
    if (now >= nextTailTime) {
      if (random(100) < 20) {
        int tailTarget = 90 + random(-25, 26);  // 随机偏移
        setServoAngle(13, tailTarget);
        tailAtNeutral = false;
      } else if (!tailAtNeutral) {
        setServoAngle(13, 90);  // 回中立（仅不在中立时发送）
        tailAtNeutral = true;
      }
      nextTailTime = now + random(2000, 5000);
    }
    
    // 左耳：随机转动（20%概率，独立于右耳）
    if (now >= nextLeftEarTime) {
      if (random(100) < 20) {
        int earTarget = 90 - random(5, 40);  // 左耳范围：50~85
        setServoAngle(14, earTarget);
        leftEarAtNeutral = false;
      } else if (!leftEarAtNeutral) {
        setServoAngle(14, 90);  // 回中立（仅不在中立时发送）
        leftEarAtNeutral = true;
      }
      nextLeftEarTime = now + random(3000, 7000);
    }
    
    // 右耳：随机转动（20%概率，独立于左耳，不对称）
    if (now >= nextRightEarTime) {
      if (random(100) < 20) {
        int earTarget = 90 + random(5, 40);  // 右耳范围：95~130
        setServoAngle(15, earTarget);
        rightEarAtNeutral = false;
      } else if (!rightEarAtNeutral) {
        setServoAngle(15, 90);  // 回中立（仅不在中立时发送）
        rightEarAtNeutral = true;
      }
      nextRightEarTime = now + random(3000, 7000);
    }
    
    vTaskDelay(pdMS_TO_TICKS(50));
  }
  
  // 待机结束，耳朵尾巴回中立
  setServoAngle(13, 90);
  delay(10);
  setServoAngle(14, 90);
  delay(10);
  setServoAngle(15, 90);
}

// ============ 坐下动作 ============
void runSitAction() {
  resetLegsToCenter();
  vTaskDelay(pdMS_TO_TICKS(100));
  
  // 耳朵放松下垂（坐下时放松状态）
  setServoAngle(14, 60);   // 左耳稍微耷拉
  delay(10);
  setServoAngle(15, 120);  // 右耳稍微耷拉
  
  // 后腿位置
  int leftBackSit = 2048 + SIT_BACK_ANGLE;   // 2826
  int rightBackSit = 2048 - SIT_BACK_ANGLE;  // 1270
  
  // 后腿一只只倒下
  Serial.println("[SIT] 左后腿倒下...");
  stsMove(3, leftBackSit, 600, 150);
  vTaskDelay(pdMS_TO_TICKS(350));
  
  Serial.println("[SIT] 右后腿倒下...");
  stsMove(4, rightBackSit, 600, 150);
  vTaskDelay(pdMS_TO_TICKS(350));
  
  // 前腿随机动作循环
  uint32_t nextTailWag = millis() + random(3000, 6000);
  while (gaitRunning && currentGait == GAIT_SIT) {
    // 偶尔摇尾巴（坐着时悠闲摆尾）
    uint32_t sitNow = millis();
    if (sitNow >= nextTailWag) {
      if (random(100) < 30) {
        // 小幅慵懒摇尾 2-3 下
        int wags = random(2, 4);
        for (int w = 0; w < wags && gaitRunning; w++) {
          setServoAngle(13, 90 + random(10, 20));
          vTaskDelay(pdMS_TO_TICKS(200));
          setServoAngle(13, 90 - random(10, 20));
          vTaskDelay(pdMS_TO_TICKS(200));
        }
        setServoAngle(13, 90);
      }
      nextTailWag = millis() + random(4000, 8000);
    }
    
    int action = random(3);
    
    if (action == 0) {
      // 往前动一动
      int amplitude = random(150, 350);
      if (random(2) == 0) {
        stsMove(1, 2048 - amplitude, random(400, 800), 100);
      } else {
        stsMove(2, 2048 + amplitude, random(400, 800), 100);
      }
      vTaskDelay(pdMS_TO_TICKS(random(300, 600)));
      stsMove(1, 2048, 400, 80);
      stsMove(2, 2048, 400, 80);
    } else if (action == 1) {
      // 俯卧撑
      int pushupCount = random(2, 5);
      for (int j = 0; j < pushupCount && gaitRunning; j++) {
        int amp = random(200, 400);
        stsMove(1, 2048 + amp, 600, 150);
        stsMove(2, 2048 - amp, 600, 150);
        vTaskDelay(pdMS_TO_TICKS(250));
        stsMove(1, 2048, 500, 120);
        stsMove(2, 2048, 500, 120);
        vTaskDelay(pdMS_TO_TICKS(250));
      }
    } else {
      // 交替动
      int count = random(3, 7);
      for (int j = 0; j < count && gaitRunning; j++) {
        int amp = random(150, 300);
        if (j % 2 == 0) {
          stsMove(1, 2048 - amp, 500, 120);
          stsMove(2, 2048 - amp, 500, 120);
        } else {
          stsMove(1, 2048 + amp, 500, 120);
          stsMove(2, 2048 + amp, 500, 120);
        }
        vTaskDelay(pdMS_TO_TICKS(200));
      }
      stsMove(1, 2048, 400, 80);
      stsMove(2, 2048, 400, 80);
    }
    
    vTaskDelay(pdMS_TO_TICKS(random(500, 1500)));
  }
  
  // 站起来
  Serial.println("[SIT] 站起来...");
  setServoAngle(13, 90);  // 尾巴回中
  delay(10);
  setServoAngle(14, 90);  // 左耳回中
  delay(10);
  setServoAngle(15, 90);  // 右耳回中
  stsMove(1, 2048, 800, 150);
  stsMove(2, 2048, 800, 150);
  vTaskDelay(pdMS_TO_TICKS(150));
  stsMove(4, 2048, 700, 150);
  vTaskDelay(pdMS_TO_TICKS(250));
  stsMove(3, 2048, 700, 150);
  vTaskDelay(pdMS_TO_TICKS(250));
}

// ============ 跳跃动作 ============
void runJumpAction() {
  resetLegsToCenter();
  
  // 耳朵竖起（跳跃警觉状态）
  setServoAngle(14, 10);   // 左耳竖起
  delay(10);
  setServoAngle(15, 170);  // 右耳竖起
  
  while (gaitRunning && currentGait == GAIT_JUMP) {
    // 随机收缩幅度
    int contraction = random(333, 556);
    int baseOvershoot = random(180, 280);
    
    // 转向调整
    int turnAdjust = (int)(turnFactor * turnStrength * baseOvershoot);
    int leftOvershoot = constrain(baseOvershoot + turnAdjust, 100, 400);
    int rightOvershoot = constrain(baseOvershoot - turnAdjust, 100, 400);
    
    Serial.println("[JUMP] 收缩...");
    
    // 收缩时尾巴收紧（向下压）
    setServoAngle(13, 65);
    
    // 收缩（所有腿向内收）
    stsMove(1, 2048 - contraction, 500, 100);
    stsMove(2, 2048 + contraction, 500, 100);
    stsMove(3, 2048 + contraction, 500, 100);
    stsMove(4, 2048 - contraction, 500, 100);
    
    vTaskDelay(pdMS_TO_TICKS(600));
    if (!gaitRunning) break;
    
    Serial.println("[JUMP] 后腿弹出!");
    // 弹出时尾巴甩高
    setServoAngle(13, 130);
    
    // 后腿先弹出
    stsMove(3, 2048 - leftOvershoot, 0, 254);   // 最大速度
    stsMove(4, 2048 + rightOvershoot, 0, 254);
    
    vTaskDelay(pdMS_TO_TICKS(200));
    if (!gaitRunning) break;
    
    Serial.println("[JUMP] 前腿弹出!");
    // 前腿弹出
    stsMove(1, 2048 + leftOvershoot, 0, 254);
    stsMove(2, 2048 - rightOvershoot, 0, 254);
    
    vTaskDelay(pdMS_TO_TICKS(100));
    if (!gaitRunning) break;
    
    // 落地，尾巴回中
    Serial.println("[JUMP] 落地");
    setServoAngle(13, 90);
    for (int id = 1; id <= 4; id++) {
      stsMove(id, 2048, 800, 150);
    }
    
    vTaskDelay(pdMS_TO_TICKS(400));
    
    // 跳跃间隔
    vTaskDelay(pdMS_TO_TICKS(random(800, 2000)));
  }
  
  // 跳跃结束，耳朵尾巴回中立
  setServoAngle(13, 90);
  delay(10);
  setServoAngle(14, 90);
  delay(10);
  setServoAngle(15, 90);
}

// ============ 快走直线步态 ============
// 与倒退严格镜像：同一 BACKWARD_CONFIG、同一周期与相位差，仅阶段1/2对调（ready→front→back→neutral→ready）
void runTrotStraightCycle() {
  for (int i = 0; i < 4; i++) {
    stsMove(i + 1, BACKWARD_CONFIG[i].ready, 800, 150);
  }
  vTaskDelay(pdMS_TO_TICKS(200));
  
  int legPhase[4] = {0, 0, 0, 0};
  uint32_t now = millis();
  uint32_t legStartTime[4];
  bool tailLeft_ts = false;
  uint32_t nextTailSwitch_ts = now;
  
  for (int i = 0; i < 4; i++) {
    int phaseOffset = BACKWARD_CONFIG[i].phase;
    legStartTime[i] = now - (uint32_t)(phaseOffset * BACKWARD_PHASE_DELAY * 1000);
  }
  
  while (gaitRunning && currentGait == GAIT_TROT_STRAIGHT) {
    now = millis();
    
    for (int i = 0; i < 4; i++) {
      int id = i + 1;
      const LegConfig& cfg = BACKWARD_CONFIG[i];
      
      float t = (now - legStartTime[i]) / 1000.0f;
      t = fmod(t, BACKWARD_CYCLE_TOTAL);
      
      float turnAdjust = 0;
      if (turnFactor != 0) {
        if (id == 1 || id == 3) turnAdjust = turnFactor * turnStrength * 200;
        else turnAdjust = -turnFactor * turnStrength * 200;
      }
      
      float t1 = BACKWARD_PHASE_1;
      float t2 = t1 + BACKWARD_PHASE_2;
      float t3 = t2 + BACKWARD_PHASE_3;
      
      int targetPos, speed, accel;
      // 直线走 = 倒退的逆向：先到 front(后) 再推到 back(前)，与倒退的 back→front 相反
      if (t < t1) {
        // 阶段1: ready→front（蓄力到“后”）
        targetPos = cfg.front + (int)turnAdjust;
        speed = calcSpeed(abs(cfg.front - cfg.ready), BACKWARD_PHASE_1);
        accel = BACKWARD_ACCEL_FAST;
      } else if (t < t2) {
        // 阶段2: front→back（推出到“前”）
        targetPos = cfg.back + (int)turnAdjust;
        speed = calcSpeed(abs(cfg.back - cfg.front), BACKWARD_PHASE_2);
        accel = BACKWARD_ACCEL_FAST;
      } else if (t < t3) {
        // 阶段3: back→neutral 回中
        targetPos = cfg.neutral + (int)turnAdjust;
        speed = calcSpeed(abs(cfg.neutral - cfg.back), BACKWARD_PHASE_3);
        accel = BACKWARD_ACCEL_TRANS;
      } else {
        // 阶段4: neutral→ready 收回
        targetPos = cfg.ready + (int)turnAdjust;
        speed = calcSpeed(abs(cfg.ready - cfg.neutral), BACKWARD_PHASE_4);
        accel = BACKWARD_ACCEL_SLOW;
      }
      
      int newPhase = (t < t1) ? 0 : (t < t2) ? 1 : (t < t3) ? 2 : 3;
      if (newPhase != legPhase[i]) {
        legPhase[i] = newPhase;
        stsMove(id, targetPos, speed, accel);
      }
    }
    
    if (now >= nextTailSwitch_ts) {
      tailLeft_ts = !tailLeft_ts;
      setServoAngle(13, tailLeft_ts ? 70 : 110);
      nextTailSwitch_ts = now + random(2500, 4500);
    }
    
    vTaskDelay(pdMS_TO_TICKS(8));
  }
  
  setServoAngle(13, 90);
}

// ============ 后退步态 ============
// 基于跑步配置，交换前后位置实现后退，使用跳跑时间参数
void runBackwardCycle() {
  // 初始化
  for (int i = 0; i < 4; i++) {
    stsMove(i + 1, BACKWARD_CONFIG[i].ready, 800, 150);
  }
  vTaskDelay(pdMS_TO_TICKS(200));
  
  int legPhase[4] = {0, 0, 0, 0};
  uint32_t now = millis();
  uint32_t legStartTime[4];
  bool tailLeft_bk = false;
  uint32_t nextTailSwitch_bk = now;
  
  // 顺序步态：与跑步相同的相位关系
  for (int i = 0; i < 4; i++) {
    int phaseOffset = BACKWARD_CONFIG[i].phase;
    legStartTime[i] = now - (uint32_t)(phaseOffset * BACKWARD_PHASE_DELAY * 1000);
  }
  
  while (gaitRunning && currentGait == GAIT_BACKWARD) {
    now = millis();
    
    for (int i = 0; i < 4; i++) {
      int id = i + 1;
      const LegConfig& cfg = BACKWARD_CONFIG[i];
      
      float t = (now - legStartTime[i]) / 1000.0f;
      t = fmod(t, BACKWARD_CYCLE_TOTAL);
      
      // 转向调整
      float turnAdjust = 0;
      if (turnFactor != 0) {
        if (id == 1 || id == 3) {
          turnAdjust = turnFactor * turnStrength * 200;
        } else {
          turnAdjust = -turnFactor * turnStrength * 200;
        }
      }
      
      float t1 = BACKWARD_PHASE_1;
      float t2 = t1 + BACKWARD_PHASE_2;
      float t3 = t2 + BACKWARD_PHASE_3;
      
      int targetPos, speed, accel;
      
      if (t < t1) {
        // 阶段1: ready→back 蓄力
        targetPos = cfg.back + (int)turnAdjust;
        speed = calcSpeed(abs(cfg.back - cfg.ready), BACKWARD_PHASE_1);
        accel = BACKWARD_ACCEL_FAST;
      } else if (t < t2) {
        // 阶段2: back→front 推出（反向运动）
        targetPos = cfg.front + (int)turnAdjust;
        speed = calcSpeed(abs(cfg.front - cfg.back), BACKWARD_PHASE_2);
        accel = BACKWARD_ACCEL_FAST;
      } else if (t < t3) {
        // 阶段3: front→neutral 回中
        targetPos = cfg.neutral + (int)turnAdjust;
        speed = calcSpeed(abs(cfg.neutral - cfg.front), BACKWARD_PHASE_3);
        accel = BACKWARD_ACCEL_TRANS;
      } else {
        // 阶段4: neutral→ready 收回
        targetPos = cfg.ready + (int)turnAdjust;
        speed = calcSpeed(abs(cfg.ready - cfg.neutral), BACKWARD_PHASE_4);
        accel = BACKWARD_ACCEL_SLOW;
      }
      
      int newPhase = (t < t1) ? 0 : (t < t2) ? 1 : (t < t3) ? 2 : 3;
      if (newPhase != legPhase[i]) {
        legPhase[i] = newPhase;
        stsMove(id, targetPos, speed, accel);
      }
    }
    
    // 尾巴摆动，低频率
    if (now >= nextTailSwitch_bk) {
      tailLeft_bk = !tailLeft_bk;
      setServoAngle(13, tailLeft_bk ? 70 : 110);
      nextTailSwitch_bk = now + random(2500, 4500);  // 2.5~4.5秒一次
    }
    
    vTaskDelay(pdMS_TO_TICKS(8));
  }
  
  // 结束，尾巴回中
  setServoAngle(13, 90);
}

// ============ 效率走步态 ============
// 交替对角：同一时刻只有一对对角腿在做推进，另一对保持中立不发力，避免前后力互相抵消
void runEfficiencyWalkCycle() {
  for (int i = 0; i < 4; i++) {
    stsMove(i + 1, BACKWARD_CONFIG[i].ready, 800, 150);
  }
  vTaskDelay(pdMS_TO_TICKS(200));
  
  int legPhase[4] = {-1, -1, -1, -1};  // -1 = 未更新
  uint32_t cycleStart = millis();
  bool tailLeft_ew = false;
  uint32_t nextTailSwitch_ew = millis();
  const float EFF_TOTAL = 2.0f;  // 总周期 2s，每对角 1s
  
  while (gaitRunning && currentGait == GAIT_EFFICIENT_WALK) {
    uint32_t now = millis();
    float t_cycle = fmod((now - cycleStart) / 1000.0f, EFF_TOTAL);
    // 前半周期 [0,1): 对角1(左前+右后, id=1,4) 推进；对角2 保持中立
    // 后半周期 [1,2): 对角2(右前+左后, id=2,3) 推进；对角1 保持中立
    bool diagonal1Active = (t_cycle < 1.0f);
    float t_local = diagonal1Active ? t_cycle : (t_cycle - 1.0f);
    
    float t1 = EFF_WALK_PHASE_1;
    float t2 = t1 + EFF_WALK_PHASE_2;
    float t3 = t2 + EFF_WALK_PHASE_3;
    
    for (int i = 0; i < 4; i++) {
      int id = i + 1;
      const LegConfig& cfg = BACKWARD_CONFIG[i];
      bool thisLegActive = (i == 0 || i == 3) ? diagonal1Active : !diagonal1Active;
      
      float turnAdjust = 0;
      if (turnFactor != 0) {
        if (id == 1 || id == 3) turnAdjust = turnFactor * turnStrength * 200;
        else turnAdjust = -turnFactor * turnStrength * 200;
      }
      
      int targetPos, speed, accel;
      if (!thisLegActive) {
        // 非推进侧保持 ready，不参与发力，下一半周期从 ready 直接开始
        targetPos = cfg.ready + (int)turnAdjust;
        speed = 800;
        accel = BACKWARD_ACCEL_SLOW;
      } else {
        // 直线走方向: ready→front→back→neutral→ready
        if (t_local < t1) {
          targetPos = cfg.front + (int)turnAdjust;
          speed = calcSpeed(abs(cfg.front - cfg.ready), EFF_WALK_PHASE_1);
          accel = BACKWARD_ACCEL_FAST;
        } else if (t_local < t2) {
          targetPos = cfg.back + (int)turnAdjust;
          speed = calcSpeed(abs(cfg.back - cfg.front), EFF_WALK_PHASE_2);
          accel = BACKWARD_ACCEL_FAST;
        } else if (t_local < t3) {
          targetPos = cfg.neutral + (int)turnAdjust;
          speed = calcSpeed(abs(cfg.neutral - cfg.back), EFF_WALK_PHASE_3);
          accel = BACKWARD_ACCEL_TRANS;
        } else {
          targetPos = cfg.ready + (int)turnAdjust;
          speed = calcSpeed(abs(cfg.ready - cfg.neutral), EFF_WALK_PHASE_4);
          accel = BACKWARD_ACCEL_SLOW;
        }
      }
      
      int newPhase = !thisLegActive ? 4 : (t_local < t1 ? 0 : (t_local < t2 ? 1 : (t_local < t3 ? 2 : 3)));
      if (newPhase != legPhase[i]) {
        legPhase[i] = newPhase;
        stsMove(id, targetPos, speed, accel);
      }
    }
    
    if (now >= nextTailSwitch_ew) {
      tailLeft_ew = !tailLeft_ew;
      setServoAngle(13, tailLeft_ew ? 70 : 110);
      nextTailSwitch_ew = now + random(2500, 4500);
    }
    
    vTaskDelay(pdMS_TO_TICKS(8));
  }
  
  setServoAngle(13, 90);
}

// ============ 四腿往复步态 ============
// 四条腿同时来回运动，无相位差，每腿单循环4阶段
void runWaveCycle() {
  // 所有腿回到中立位
  for (int i = 0; i < 4; i++) {
    stsMove(i + 1, TROT_CONFIG[i].neutral, 800, 150);
  }
  vTaskDelay(pdMS_TO_TICKS(200));
  
  uint32_t startTime = millis();
  bool tailLeft_wv = false;
  uint32_t nextTailSwitch_wv = millis();
  int lastPos[4] = {0, 0, 0, 0};  // 记录上次位置，避免重复发送
  
  while (gaitRunning && currentGait == GAIT_WAVE) {
    uint32_t now = millis();
    float t = fmod((now - startTime) / 1000.0f, WAVE_CYCLE_TOTAL);
    
    // 使用正弦波生成流畅的往复运动
    // sin值从-1到1，映射到幅度范围
    float angle = (t / WAVE_CYCLE_TOTAL) * 2.0f * PI;
    float sinVal = sin(angle);
    
    // 所有腿同时流畅运动
    for (int i = 0; i < 4; i++) {
      int id = i + 1;
      const LegConfig& cfg = TROT_CONFIG[i];
      
      // 以neutral为中心，正弦波驱动前后运动
      int offset = (int)(sinVal * WAVE_AMPLITUDE);
      int targetPos = cfg.neutral + offset;
      
      // 避免重复发送相同位置（减少通信开销）
      if (abs(targetPos - lastPos[i]) > 10) {
        stsMove(id, targetPos, WAVE_SPEED, WAVE_ACCEL);
        lastPos[i] = targetPos;
      }
    }
    
    // 尾巴随动摆动，低频率
    if (now >= nextTailSwitch_wv) {
      tailLeft_wv = !tailLeft_wv;
      setServoAngle(13, tailLeft_wv ? 70 : 110);
      nextTailSwitch_wv = now + random(2500, 4500);  // 2.5~4.5秒一次
    }
    
    vTaskDelay(pdMS_TO_TICKS(20));  // 更高频率检查，保持流畅
  }
  
  // 结束，回中立
  setServoAngle(13, 90);
}

// ============ 倒下动作 ============
// 类似坐下，但后腿向后（反方向），前腿也向前倒
void runLaydownAction() {
  resetLegsToCenter();
  vTaskDelay(pdMS_TO_TICKS(100));
  
  // 耳朵耷拉（倒下时无力状态）
  setServoAngle(14, 50);
  delay(10);
  setServoAngle(15, 130);
  
  // 后腿向后倒（与坐下相反方向）
  // 左后腿：低值=后方，右后腿：高值=后方
  int leftBackDown = 2048 - LAYDOWN_BACK_ANGLE;    // 1270
  int rightBackDown = 2048 + LAYDOWN_BACK_ANGLE;   // 2826
  
  // 前腿向前倒
  // 左前腿：高值=前方，右前腿：低值=前方
  int leftFrontDown = 2048 + LAYDOWN_FRONT_ANGLE;  // 2826
  int rightFrontDown = 2048 - LAYDOWN_FRONT_ANGLE; // 1270
  
  Serial.println("[LAYDOWN] 后腿向后倒...");
  stsMove(3, leftBackDown, 600, 150);
  vTaskDelay(pdMS_TO_TICKS(350));
  stsMove(4, rightBackDown, 600, 150);
  vTaskDelay(pdMS_TO_TICKS(350));
  
  Serial.println("[LAYDOWN] 前腿向前倒...");
  stsMove(1, leftFrontDown, 600, 150);
  vTaskDelay(pdMS_TO_TICKS(350));
  stsMove(2, rightFrontDown, 600, 150);
  vTaskDelay(pdMS_TO_TICKS(350));
  
  Serial.println("[LAYDOWN] 已倒下");
  
  // 尾巴放平
  setServoAngle(13, 90);
  
  // 倒下后的等待循环（偶尔动一动）
  uint32_t nextTailTime = millis() + random(3000, 6000);
  uint32_t nextEarTime = millis() + random(4000, 8000);
  uint32_t nextLegTime = millis() + random(5000, 10000);
  
  while (gaitRunning && currentGait == GAIT_LAYDOWN) {
    uint32_t now = millis();
    
    // 偶尔尾巴微弱摆动
    if (now >= nextTailTime) {
      if (random(100) < 25) {
        int wags = random(1, 3);
        for (int w = 0; w < wags && gaitRunning; w++) {
          setServoAngle(13, 90 + random(8, 15));
          vTaskDelay(pdMS_TO_TICKS(250));
          setServoAngle(13, 90 - random(8, 15));
          vTaskDelay(pdMS_TO_TICKS(250));
        }
        setServoAngle(13, 90);
      }
      nextTailTime = millis() + random(4000, 8000);
    }
    
    // 偶尔耳朵抽动一下
    if (now >= nextEarTime) {
      if (random(100) < 20) {
        setServoAngle(14, 50 + random(-10, 15));
        delay(10);
        setServoAngle(15, 130 + random(-15, 10));
        vTaskDelay(pdMS_TO_TICKS(500));
        setServoAngle(14, 50);
        delay(10);
        setServoAngle(15, 130);
      }
      nextEarTime = millis() + random(5000, 10000);
    }
    
    // 偶尔随机某条腿缓慢移动一小段距离
    if (now >= nextLegTime) {
      if (random(100) < 30) {
        int legId = random(1, 5);  // 随机选择1-4号腿
        int direction = random(0, 2) ? 1 : -1;  // 随机方向
        int offset = random(50, 150) * direction;  // 小幅度移动
        
        // 获取当前腿的倒下位置
        int currentPos;
        if (legId == 1) currentPos = leftFrontDown;
        else if (legId == 2) currentPos = rightFrontDown;
        else if (legId == 3) currentPos = leftBackDown;
        else currentPos = rightBackDown;
        
        // 缓慢移动到新位置
        stsMove(legId, currentPos + offset, 300, 80);
        vTaskDelay(pdMS_TO_TICKS(800));
        
        // 缓慢回到倒下位置
        stsMove(legId, currentPos, 300, 80);
      }
      nextLegTime = millis() + random(6000, 12000);
    }
    
    vTaskDelay(pdMS_TO_TICKS(100));
  }
  
  // 站起来
  Serial.println("[LAYDOWN] 站起来...");
  setServoAngle(13, 90);
  delay(10);
  setServoAngle(14, 90);
  delay(10);
  setServoAngle(15, 90);
  
  // 先收回前腿
  stsMove(1, 2048, 800, 150);
  stsMove(2, 2048, 800, 150);
  vTaskDelay(pdMS_TO_TICKS(300));
  
  // 再收回后腿
  stsMove(3, 2048, 700, 150);
  vTaskDelay(pdMS_TO_TICKS(250));
  stsMove(4, 2048, 700, 150);
  vTaskDelay(pdMS_TO_TICKS(250));
}

// ============ 拜年动作 ============
// 前腿在上和中立位之间缓慢运动，后腿坐姿，耳朵偶尔动
void runNewYearAction() {
  Serial.println("[NEWYEAR] 拜年开始...");
  
  // 先回到中立位
  resetLegsToCenter();
  vTaskDelay(pdMS_TO_TICKS(100));
  
  // 后腿采用坐姿（像坐下动作）
  Serial.println("[NEWYEAR] 后腿坐下...");
  stsMove(3, NEWYEAR_BACK_LEFT, 600, 150);
  vTaskDelay(pdMS_TO_TICKS(350));
  stsMove(4, NEWYEAR_BACK_RIGHT, 600, 150);
  vTaskDelay(pdMS_TO_TICKS(350));
  
  // 前腿先到中立位
  stsMove(1, NEWYEAR_LEFT_MID, 800, 150);
  stsMove(2, NEWYEAR_RIGHT_MID, 800, 150);
  vTaskDelay(pdMS_TO_TICKS(200));
  
  // 耳朵竖起来（精神状态）
  setServoAngle(14, 90);
  delay(10);
  setServoAngle(15, 90);
  
  uint32_t startTime = millis();
  uint32_t nextEarTime = millis() + random(4000, 8000);
  bool tailLeft_ny = false;
  uint32_t nextTailSwitch_ny = millis() + random(3000, 5000);
  
  int phase = 0;  // 0=向上, 1=向下
  
  while (gaitRunning && currentGait == GAIT_NEWYEAR) {
    uint32_t now = millis();
    
    // 前腿缓慢在高位和中位之间运动（拜年动作）
    // 左前腿：增大=向上，右前腿：减小=向上
    if (phase == 0) {
      // 向上抬起
      stsMove(1, NEWYEAR_LEFT_HIGH, NEWYEAR_SPEED, NEWYEAR_ACCEL);   // 左前腿向上
      stsMove(2, NEWYEAR_RIGHT_HIGH, NEWYEAR_SPEED, NEWYEAR_ACCEL);  // 右前腿向上（低值）
      vTaskDelay(pdMS_TO_TICKS((int)(NEWYEAR_CYCLE_TOTAL * 500)));  // 半周期
      phase = 1;
    } else {
      // 向下回到中位
      stsMove(1, NEWYEAR_LEFT_MID, NEWYEAR_SPEED, NEWYEAR_ACCEL);
      stsMove(2, NEWYEAR_RIGHT_MID, NEWYEAR_SPEED, NEWYEAR_ACCEL);
      vTaskDelay(pdMS_TO_TICKS((int)(NEWYEAR_CYCLE_TOTAL * 500)));  // 半周期
      phase = 0;
    }
    
    now = millis();
    
    // 偶尔耳朵抽动一下
    if (now >= nextEarTime) {
      if (random(100) < 30) {
        setServoAngle(14, 90 + random(-15, 15));
        delay(10);
        setServoAngle(15, 90 + random(-15, 15));
        vTaskDelay(pdMS_TO_TICKS(400));
        setServoAngle(14, 90);
        delay(10);
        setServoAngle(15, 90);
      }
      nextEarTime = millis() + random(5000, 10000);
    }
    
    // 尾巴轻柔摆动
    if (now >= nextTailSwitch_ny) {
      tailLeft_ny = !tailLeft_ny;
      setServoAngle(13, tailLeft_ny ? 75 : 105);
      nextTailSwitch_ny = now + random(2000, 4000);
    }
  }
  
  // 结束，恢复正常姿态
  Serial.println("[NEWYEAR] 拜年结束，站起来...");
  
  // 耳朵和尾巴回中
  setServoAngle(13, 90);
  delay(10);
  setServoAngle(14, 90);
  delay(10);
  setServoAngle(15, 90);
  
  // 前腿先回到中立位
  stsMove(1, 2048, 800, 150);
  stsMove(2, 2048, 800, 150);
  vTaskDelay(pdMS_TO_TICKS(300));
  
  // 后腿站起来
  stsMove(3, 2048, 700, 150);
  vTaskDelay(pdMS_TO_TICKS(250));
  stsMove(4, 2048, 700, 150);
  vTaskDelay(pdMS_TO_TICKS(250));
}

// ============ 马失前蹄动作 ============
// 先直线走5秒，然后前腿失蹄，最后四仰八叉
void runStumbleAction() {
  Serial.println("[STUMBLE] 马失前蹄开始...");
  
  // 阶段1: 直线走5秒（与 runTrotStraightCycle 相同：BACKWARD_CONFIG + ready→front→back→neutral→ready）
  Serial.println("[STUMBLE] 阶段1: 直线走...");
  
  for (int i = 0; i < 4; i++) {
    stsMove(i + 1, BACKWARD_CONFIG[i].ready, 800, 150);
  }
  vTaskDelay(pdMS_TO_TICKS(200));
  
  int legPhase[4] = {0, 0, 0, 0};
  uint32_t walkStartTime = millis();
  uint32_t legStartTime[4];
  
  for (int i = 0; i < 4; i++) {
    int phaseOffset = BACKWARD_CONFIG[i].phase;
    legStartTime[i] = walkStartTime - (uint32_t)(phaseOffset * BACKWARD_PHASE_DELAY * 1000);
  }
  
  while (gaitRunning && currentGait == GAIT_STUMBLE && (millis() - walkStartTime < 5000)) {
    uint32_t now = millis();
    
    for (int i = 0; i < 4; i++) {
      int id = i + 1;
      const LegConfig& cfg = BACKWARD_CONFIG[i];
      
      float t = (now - legStartTime[i]) / 1000.0f;
      t = fmod(t, BACKWARD_CYCLE_TOTAL);
      
      float t1 = BACKWARD_PHASE_1;
      float t2 = t1 + BACKWARD_PHASE_2;
      float t3 = t2 + BACKWARD_PHASE_3;
      
      int targetPos, speed, accel;
      // 直线走：ready→front→back→neutral→ready（与倒退镜像）
      if (t < t1) {
        targetPos = cfg.front;
        speed = calcSpeed(abs(cfg.front - cfg.ready), BACKWARD_PHASE_1);
        accel = BACKWARD_ACCEL_FAST;
      } else if (t < t2) {
        targetPos = cfg.back;
        speed = calcSpeed(abs(cfg.back - cfg.front), BACKWARD_PHASE_2);
        accel = BACKWARD_ACCEL_FAST;
      } else if (t < t3) {
        targetPos = cfg.neutral;
        speed = calcSpeed(abs(cfg.neutral - cfg.back), BACKWARD_PHASE_3);
        accel = BACKWARD_ACCEL_TRANS;
      } else {
        targetPos = cfg.ready;
        speed = calcSpeed(abs(cfg.ready - cfg.neutral), BACKWARD_PHASE_4);
        accel = BACKWARD_ACCEL_SLOW;
      }
      
      int newPhase = (t < t1) ? 0 : (t < t2) ? 1 : (t < t3) ? 2 : 3;
      if (newPhase != legPhase[i]) {
        legPhase[i] = newPhase;
        stsMove(id, targetPos, speed, accel);
      }
    }
    
    vTaskDelay(pdMS_TO_TICKS(8));
  }
  
  if (!gaitRunning || currentGait != GAIT_STUMBLE) return;
  
  // 阶段2: 前腿失蹄（快速向后倒）
  Serial.println("[STUMBLE] 阶段2: 前腿失蹄！");
  
  // 耳朵向后（惊恐状态）
  setServoAngle(14, 50);
  delay(10);
  setServoAngle(15, 130);
  
  // 尾巴翘起（失衡）
  setServoAngle(13, 120);
  
  // 左前腿先失蹄（略微有相位差）
  // 左前腿：向后转动（数值减小，角度加倍至800单位）
  stsMove(1, 2198 - 800, 2000, 254);  // 快速向后，大幅度
  vTaskDelay(pdMS_TO_TICKS(80));  // 80ms相位差
  
  // 右前腿失蹄
  // 右前腿：向后转动（数值增大，角度加倍至800单位）
  stsMove(2, 1898 + 800, 2000, 254);  // 快速向后，大幅度
  
  // 等待前腿失蹄完成
  vTaskDelay(pdMS_TO_TICKS(300));
  
  if (!gaitRunning || currentGait != GAIT_STUMBLE) return;
  
  // 阶段3: 四仰八叉（1秒后）
  Serial.println("[STUMBLE] 阶段3: 四仰八叉...");
  vTaskDelay(pdMS_TO_TICKS(1000));
  
  if (!gaitRunning || currentGait != GAIT_STUMBLE) return;
  
  // 四条腿急促往复运动，模仿四仰八叉挣扎
  uint32_t struggleStart = millis();
  uint32_t struggleDuration = 3000;  // 挣扎3秒
  
  // 所有腿先回到中立位附近
  for (int i = 1; i <= 4; i++) {
    stsMove(i, 2048, 1500, 200);
  }
  vTaskDelay(pdMS_TO_TICKS(200));
  
  while (gaitRunning && currentGait == GAIT_STUMBLE && (millis() - struggleStart < struggleDuration)) {
    // 四条腿同时或分别快速往复运动
    int legId = random(1, 5);
    
    // 在中立位（2048）附近大幅度往复运动
    int direction = random(0, 2) ? 1 : -1;
    int movement = random(150, 300) * direction;  // 增大运动幅度
    
    // 所有腿都基于中立位（2048）运动
    stsMove(legId, 2048 + movement, 800, 200);  // 速度更快，更急促
    
    // 偶尔同时动两条腿
    if (random(100) < 40) {
      int legId2 = random(1, 5);
      if (legId2 != legId) {
        int movement2 = random(150, 300) * (random(0, 2) ? 1 : -1);
        stsMove(legId2, 2048 + movement2, 800, 200);
      }
    }
    
    // 尾巴频繁抽动（更急促）
    if (random(100) < 50) {
      setServoAngle(13, 90 + random(-30, 30));
    }
    
    // 耳朵频繁动
    if (random(100) < 40) {
      setServoAngle(14, 50 + random(-15, 25));
      setServoAngle(15, 130 + random(-25, 15));
    }
    
    // 缩短间隔，动作更急促
    vTaskDelay(pdMS_TO_TICKS(random(80, 200)));
  }
  
  if (!gaitRunning || currentGait != GAIT_STUMBLE) return;
  
  // 结束：慢慢站起来
  Serial.println("[STUMBLE] 站起来...");
  
  // 耳朵和尾巴回中
  setServoAngle(13, 90);
  delay(10);
  setServoAngle(14, 90);
  delay(10);
  setServoAngle(15, 90);
  
  // 前腿慢慢回到中立位
  stsMove(1, 2048, 600, 100);
  stsMove(2, 2048, 600, 100);
  vTaskDelay(pdMS_TO_TICKS(500));
  
  // 后腿回中
  stsMove(3, 2048, 600, 100);
  stsMove(4, 2048, 600, 100);
  vTaskDelay(pdMS_TO_TICKS(500));
  
  Serial.println("[STUMBLE] 马失前蹄结束");
}

