# 见舟 · 自媒体运营工作台

本地优先的个人创作者运营工作台，当前处于架构重建阶段。技术栈为 FastAPI、SQLite 和免构建 Vue 3。

## 当前状态

- 首次启动由 `JIANZHOU_DEMO` 决定初始模式；之后以“系统设置 → 运行模式”保存的选择为准，并可在运行中立即切换。
- 演示模式使用明确标识的确定性 Provider；真实模式使用已保存的数据源和模型配置，缺失能力会显式失败，不会自动回退为演示数据。
- Phase 0/1 已完成启动、数据库迁移、密钥、日志和打包底座重建。
- Phase 2–5 已接通可靠任务、素材/上下文、真实 ASR/LLM、发布快照复盘与核心前端链路；未实现能力仍在矩阵中明确标注。
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
- `JIANZHOU_DEMO=0`：仅设置新数据目录首次启动时的初始模式；已有数据目录以持久化设置为准。
- `JIANZHOU_TIKHUB_API_KEY`、`JIANZHOU_LLM_API_KEY`、`JIANZHOU_ASR_API_KEY`：以环境变量提供密钥，不落盘。
- `JIANZHOU_PROVIDER_HOSTS`：逗号分隔的额外可信 TikHub Provider 域名。

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
真实 Provider 低频 E2E 使用 `python scripts/real_chain_test.py BASE_URL AUDIO_FILE`；脚本不包含也不输出 Key。

## 内容雷达与对标账号

进入“内容雷达”，可通过顶部“添加对标账号”按钮或“对标账号”页签管理监控对象。入口支持抖音、小红书、视频号、B站、快手、微博和 TikTok，并可自填 1–500 条目标抓取数量；包含账号去重、分页同步、作品筛选、作者分析、提取文案及转入雷达信号。

- Demo 模式会明确显示“演示数据”，用于验证完整产品流程。
- Real 模式必须先在“系统设置 → 数据源”配置 TikHub API Key；未配置时直接返回可诊断错误，不创建空账号，也不会回退成演示数据。
- 抖音已使用真实短链完成“解析账号 → 拉取账号概况 → 按目标条数分页 → 入库”的端到端验收。其余平台已按 TikHub 官方接口建立映射，但在取得各平台真实样本并完成契约测试前仍标记为 `PARTIAL`。
- 导航分组默认全部展开，页面切换不会自动收起；收起状态只由用户手动点击并保存在本机浏览器。

## 模型与运行模式设置

“系统设置 → 大模型”支持 OpenAI Compatible 服务，可配置 API Base URL、Bearer/无鉴权方式和四类任务模型名称；模型名称既可手填，也可从服务端 `/models` 读取。保存不会触发计费，“测试快速模型”会通过 `/chat/completions` 发起一次最小真实请求。

- 公网自定义服务必须使用 HTTPS；本机或私有网络服务只有在用户显式开启后才允许使用 HTTP。
- API Key 使用 Windows DPAPI 保存，设置接口只返回“是否已配置”。
- 快速分类、深度分析、写作和结构化抽取已接入任务路由；DeepSeek 已完成真实业务 E2E。Embedding 仍明确未接入。
- “系统设置 → 语音模型”支持阿里云百炼 `qwen3-asr-flash` 的 Base URL、模型、语言、DPAPI Key 和真实音频测试；TTS 仍明确未接入。

## 打包

只有在源码功能与测试验收完成、且用户明确要求后才执行打包。本轮未重新构建 EXE。

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
- `docs/Phase2_内容雷达与对标账号闭环.md`
- `docs/Phase2.1_对标平台与扫描策略修订.md`
- `docs/Phase2.2_系统设置与模型Provider闭环.md`
- `docs/Phase2-5_真实链路验收报告.md`

后续开发必须以真实运行证据、自动化测试和验收门槛为完成依据。
