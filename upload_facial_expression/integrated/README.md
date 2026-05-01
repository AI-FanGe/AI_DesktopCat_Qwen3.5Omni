# ESP32S3 集成项目

## 功能概述

将 `test_all_modules` 的硬件接线与 `voiceassistant` 的语音对话功能整合，实现：

1. **屏幕显示** - ST7789 LCD 显示状态和动画
2. **摄像头** - ESP32S3 Sense 内置摄像头，WebSocket 实时传输
3. **语音输入** - PDM 麦克风 + ASR 语音识别
4. **语音输出** - MAX98357A 扬声器播放 AI 回复
5. **舵机控制** - PCA9685 (16路) + STS3032 (智能舵机)
6. **动画播放** - LittleFS 存储 JPEG 序列帧动画

## 硬件接线

按照 `test_all_modules.ino` 的接线方案：

| 模块 | 引脚 |
|------|------|
| **屏幕 ST7789** | CS=D0, DC=D1, RST=D2, SCK=D8, MOSI=D10 |
| **PCA9685** | SDA=D4, SCL=D5 |
| **STS3032舵机** | TX=D6(43), RX=D7(44), 波特率1000000 |
| **MAX98357A** | BCLK=D11(42), LRC=D12(41), DIN=D9(8) |
| **摄像头** | ESP32S3 Sense 内置 OV2640 |
| **麦克风** | ESP32S3 Sense 内置 PDM (CLK=42, DATA=41) |

> ⚠️ 注意：麦克风和扬声器共用 GPIO 42/41，需要在播放时禁用麦克风

## 目录结构

```
integrated/
├── integrated.ino      # Arduino 主程序
├── camera_pins.h       # 摄像头引脚定义
├── server/
│   ├── app.py          # Python 后端服务
│   └── requirements.txt
└── README.md
```

## 使用方法

### 1. 烧录 Arduino 代码

1. 使用 Arduino IDE 或 PlatformIO
2. 选择板卡：XIAO ESP32S3 Sense
3. 修改 WiFi 配置（SSID/密码）
4. 上传代码

### 2. 启动后端服务

```bash
cd server
pip install -r requirements.txt
python app.py
```

服务将在 `http://0.0.0.0:8081` 启动

### 3. 访问控制面板

打开浏览器访问：`http://<服务器IP>:8081`

功能包括：
- 实时视频查看
- 语音对话（需配置 DashScope API Key）
- 舵机控制
- 表情控制

## 网络通信

| 端点 | 用途 |
|------|------|
| `GET /` | Web 控制面板 |
| `WS /ws/camera` | ESP32 视频上传 |
| `WS /ws/viewer` | 浏览器观看视频 |
| `WS /ws_audio` | ESP32 音频上传 |
| `WS /ws_ui` | UI 状态推送 |
| `GET /stream.wav` | 音频流下载 |
| `GET /servo?ch=&angle=` | 舵机控制 |

## 配置 ASR

1. 获取阿里云 DashScope API Key
2. 设置环境变量：
   ```bash
   export DASHSCOPE_API_KEY=your_api_key
   ```
3. 安装依赖：
   ```bash
   pip install dashscope
   ```

## 串口命令

ESP32 支持以下串口命令：

- `h` / `?` - 显示帮助
- `s` - 扫描 STS3032 舵机
- `a1` / `a2` / `a3` - 播放动画
- `a0` - 停止动画

## 常见问题

### Q: 麦克风和扬声器冲突？
A: 两者共用 GPIO 42/41。系统会在播放时自动禁用麦克风。

### Q: 视频卡顿？
A: 检查 WiFi 信号强度，或降低视频分辨率（修改 `FRAMESIZE_VGA`）

### Q: 舵机不动？
A: 检查 I2C 连接和 PCA9685 供电（需要外部 5V 电源）















