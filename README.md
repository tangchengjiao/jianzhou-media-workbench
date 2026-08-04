# 见舟 · 自媒体运营工作台

本地优先的个人创作者运营工作台，当前处于架构重建阶段。技术栈为 FastAPI、SQLite 和免构建 Vue 3。

## 当前状态

- `JIANZHOU_DEMO=1`：使用明确标识的确定性演示 Provider，用于产品流程演示和开发测试。
- `JIANZHOU_DEMO=0`：真实模式。尚未完成的 LLM、STT 和平台能力会显式失败，不会自动回退为演示数据。
- Phase 0/1 已开始重建启动、数据库迁移、密钥、日志和打包底座。
- 页面存在或 HTTP 200 不代表真实业务能力已经实现。能力状态口径见 `docs/文档索引与实现基线.md`。

## 开发运行

要求 Python 3.11+：

```powershell
python -m pip install -r requirements.txt
$env:JIANZHOU_DEMO = "1"
python app.py
```

访问 <http://127.0.0.1:8741>。

可用环境变量：

- `JIANZHOU_PORT`：本机监听端口，默认 `8741`。
- `JIANZHOU_NO_BROWSER=1`：启动时不自动打开浏览器。
- `JIANZHOU_DATA_DIR`：覆盖运行数据目录，适合测试和迁移。
- `JIANZHOU_DEMO=0`：关闭演示模式。
- `JIANZHOU_TIKHUB_API_KEY`、`JIANZHOU_LLM_API_KEY`：以环境变量提供密钥，不落盘。
- `JIANZHOU_PROVIDER_HOSTS`：逗号分隔的额外可信 Provider 域名。

在实现请求身份认证前，服务只允许绑定 `127.0.0.1`、`localhost` 或 `::1`。

## 数据与密钥

- 开发模式：默认写入项目的 `data/`，该目录内容不会进入 Git。
- EXE 模式：默认写入 `%LOCALAPPDATA%/JianzhouMediaWorkbench/`。
- SQLite 迁移前自动创建备份到 `backups/`。
- API Key 使用当前 Windows 用户的 DPAPI 加密并存放于运行数据目录，不写入 SQLite，也不会通过设置 API 回传。

## 验证

基础测试：

```powershell
python -m unittest discover -s tests -v
```

服务启动后可以执行演示流程冒烟测试：

```powershell
python scripts/smoke_test.py
python scripts/browser_smoke_test.py
```

`scripts/smoke_test.py` 验证演示 API 流程；`scripts/browser_smoke_test.py` 使用本机
Chrome/Edge 验证 Vue 已实际挂载，用于阻止“HTML 可访问但界面白板”的回归。
两者都不代表 Real Provider 已完成验收。

## 打包

```powershell
python -m pip install --index-url https://pypi.org/simple -r requirements.lock.txt
pyinstaller packaging/pyinstaller.spec --distpath dist --workpath build --clean
```

详细说明见 `packaging/打包说明.md`。

## 文档

- `docs/产品需求文档_PRD_v2.0.md`
- `docs/PRD_v2.1_修订说明.md`
- `docs/技术开发与实现方案_v2.0.md`
- `docs/技术方案_v2.1_修订说明.md`
- `docs/文档索引与实现基线.md`

后续开发必须以真实运行证据、自动化测试和验收门槛为完成依据。
