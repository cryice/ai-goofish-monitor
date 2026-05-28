# Windows环境配置指南

## 脚本说明

本项目提供了Windows环境下的便捷脚本，用于快速启动和配置应用：

- `start.bat` - 启动脚本：检查环境、安装依赖、构建前端、启动后端服务
- `install-dependencies.bat` - 依赖安装脚本：安装Python依赖、Playwright浏览器和前端依赖

## 环境要求

在运行脚本之前，请确保系统已安装以下软件：

1. **Python 3.10+**
   - 下载地址: https://www.python.org/downloads/
   - 安装时请勾选"Add Python to PATH"

2. **Node.js (LSTM版本)**
   - 下载地址: https://nodejs.org/
   - 验证安装: `node --version`

3. **浏览器 (Chrome 或 Edge)**
   - Chrome: https://www.google.com/chrome/
   - Edge: https://www.microsoft.com/edge/

## 使用方法

### 1. 安装依赖

首次运行或环境发生变化时，请先运行依赖安装脚本：

```cmd
install-dependencies.bat
```

### 2. 启动应用

安装完成后，运行启动脚本：

```cmd
start.bat
```

### 3. 配置文件

如需自定义配置，请复制示例配置文件：

```cmd
copy .env.example .env
```

编辑 `.env` 文件，填写必要的配置项，特别是：

- `OPENAI_API_KEY` - AI模型API密钥
- `OPENAI_BASE_URL` - AI模型接口地址
- `OPENAI_MODEL_NAME` - 支持图片输入的模型名称

## 访问应用

启动成功后，可以通过以下地址访问：

- **应用主页**: http://localhost:8000
- **API文档**: http://localhost:8000/docs

## 故障排除

如果遇到问题，请检查：

1. 确保所有环境要求都已满足
2. 检查防火墙是否阻止了端口8000
3. 查看控制台输出的错误信息
4. 确认配置文件(.env)中的必要参数已填写

## 首次使用

1. 打开Web UI `http://localhost:8000` 并使用默认账号密码登录 (admin/admin123)
2. 进入"闲鱼账号管理"，导入闲鱼登录态JSON
3. 回到"任务管理"，创建并运行任务
