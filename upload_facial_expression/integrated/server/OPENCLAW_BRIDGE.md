# Server ↔ OpenClaw 桥接说明

将 server 的 ASR 语音识别文字作为 prompt 转发到 OpenClaw 对话。

## 前置：OpenClaw 开机自启

确保 OpenClaw Gateway 在开机时已启动（例如用 systemd / launchd / 启动脚本）：

```bash
openclaw gateway
# 默认监听 http://127.0.0.1:18789
```

## 启用方式

设置环境变量：

```bash
export OPENCLAW_ENABLED=1
export OPENCLAW_GATEWAY_URL=http://127.0.0.1:18789   # 可选，默认此值
export OPENCLAW_GATEWAY_TOKEN=your-token             # 可选，若 Gateway 启用认证
export OPENCLAW_SESSION_KEY=main                     # 可选，会话键，默认 main
```

或在 `.env` 中配置：

```
OPENCLAW_ENABLED=1
OPENCLAW_GATEWAY_URL=http://127.0.0.1:18789
OPENCLAW_GATEWAY_TOKEN=
OPENCLAW_SESSION_KEY=main
```

## 前置条件

1. **OpenClaw Gateway 已启动**  
   ```bash
   openclaw gateway
   ```
   默认监听 `http://127.0.0.1:18789`

2. **Gateway 若启用了 token 认证**  
   需设置 `OPENCLAW_GATEWAY_TOKEN`，与 `~/.openclaw/openclaw.json` 中配置一致。

## 工作流程

1. 用户通过麦克风说话（如「帮我干嘛」）→ ESP32 发送音频到 server
2. server 使用 DashScope ASR 识别为文字
3. 识别到「句末」后：
   - 继续走原有流程：调用 `start_ai_with_text`，通过 Omni 做 AI 对话并 TTS 播放
   - **若 `OPENCLAW_ENABLED=1`**：同时将语音文字通过 OpenClaw 的 `/v1/chat/completions` 转发为 prompt
4. OpenClaw 收到 prompt 后执行对话，在 Control UI `http://127.0.0.1:18789/` 的 Chat 中可看到你发过去的语音文字。

## 依赖

- `aiohttp`：已在 `requirements.txt` 中
