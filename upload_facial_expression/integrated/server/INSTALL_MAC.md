# Mac 安装指南

## 前置要求

1. **Python 3.9+** （推荐 3.10 或 3.11）
   - 检查版本：`python3 --version`
   - 如果没有安装，使用 Homebrew：`brew install python3`

2. **pip**（通常随 Python 一起安装）
   - 检查：`pip3 --version`

## 安装步骤

### 1. 进入项目目录

```bash
cd "/Users/fan/Desktop/小马/develop - 副本/test_all_modules/integrated/server"
```

### 2. 创建虚拟环境（推荐）

```bash
# 创建虚拟环境
python3 -m venv venv

# 激活虚拟环境
source venv/bin/activate
```

激活后，终端提示符前会显示 `(venv)`。

### 3. 升级 pip

```bash
pip install --upgrade pip
```

### 4. 安装依赖

```bash
pip install -r requirements.txt
```

### 5. 配置环境变量

创建 `.env` 文件（如果还没有）：

```bash
# 在项目目录下创建 .env 文件
cat > .env << 'EOF'
DASHSCOPE_API_KEY=sk-fake-placeholder
ASR_DEBUG_RAW=0
EOF
```

或者手动创建 `.env` 文件，内容：
```
DASHSCOPE_API_KEY=sk-fake-placeholder
ASR_DEBUG_RAW=0
```

**注意**：请将 `DASHSCOPE_API_KEY` 替换为你的实际 API Key。

### 6. 运行服务器

```bash
# 确保虚拟环境已激活
source venv/bin/activate

# 运行服务器
python app.py
```

服务器将在 `http://0.0.0.0:8081` 启动。

## 常见问题

### 问题 1: `audioop` 模块找不到（Python 3.13+）

如果使用 Python 3.13 或更高版本，`audioop` 已被移除。项目已使用 `audioop-lts` 作为替代，应该会自动安装。

### 问题 2: 端口被占用

如果 8081 端口被占用，可以修改 `app.py` 最后一行：

```python
uvicorn.run(app, host="0.0.0.0", port=8082, ...)  # 改为其他端口
```

### 问题 3: 权限问题

如果遇到权限错误，可能需要使用 `sudo`（不推荐）或修复权限：

```bash
# 修复 pip 权限
pip install --user -r requirements.txt
```

### 问题 4: 依赖安装失败

如果某些依赖安装失败，可以尝试：

```bash
# 使用国内镜像源（更快）
pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
```

## 依赖说明

主要依赖包：
- `fastapi` - Web 框架
- `uvicorn` - ASGI 服务器
- `dashscope` - 阿里云 DashScope API（ASR 和 AI 对话）
- `openai` - OpenAI 兼容客户端（用于 Omni 模型）
- `audioop-lts` - 音频处理（Python 3.13+ 兼容）
- `python-dotenv` - 环境变量管理
- `websockets` - WebSocket 支持
- `aiohttp` - 异步 HTTP 客户端

## 下次使用

每次使用前，只需：

```bash
cd "/Users/fan/Desktop/小马/develop - 副本/test_all_modules/integrated/server"
source venv/bin/activate
python app.py
```

## 退出虚拟环境

使用完毕后：

```bash
deactivate
```

