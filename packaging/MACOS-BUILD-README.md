# macOS 原生构建说明（不是可执行程序）

本源码快照对应 `2.1.0-context-20260808`，用于在 macOS 原生环境构建 `.app`。

## 为什么没有在 Windows 直接生成 macOS 应用

PyInstaller 不支持从 Windows 交叉生成可验证的 macOS Mach-O / `.app`。Apple Silicon 与 Intel 也需要分别构建和验收。

## 本机原生构建

要求：

- macOS 12 或更高版本；
- Python 3.11 或更高版本；
- 可访问 PyPI；
- 发布给第三方时，需要 Apple Developer ID Application 证书和公证权限。

执行：

```bash
chmod +x packaging/build_macos.sh
./packaging/build_macos.sh
```

输出会放到 `output/releases/`，并生成 ZIP 和 SHA-256。

## GitHub Actions

仓库包含 `.github/workflows/build-macos.yml`，可以在已配置的 GitHub 仓库中分别构建 Apple Silicon 与 Intel 工件。工作流产物仍需在真实 Mac 上完成启动、Keychain、页面和全新数据目录验收。

## 对外发布前必须完成

1. `codesign` 使用 Developer ID Application 证书签名；
2. `notarytool` 提交 Apple 公证；
3. `stapler` 附加公证票据；
4. 分别在全新 arm64 / x64 用户环境启动；
5. 验证数据写入 `~/Library/Application Support/JianzhouMediaWorkbench/`；
6. 验证 API Key 进入登录钥匙串，未写入数据库或发布 ZIP；
7. 生成最终 SHA-256 并更新用户手册。

没有完成这些步骤前，请把产物标为“测试构建”，不要描述成可直接分享的正式 macOS 版本。
