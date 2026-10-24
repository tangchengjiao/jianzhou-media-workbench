@echo off
chcp 65001 >nul
where ffmpeg >nul 2>nul
if %errorlevel%==0 (
  echo FFmpeg 已安装，无需重复操作。
  ffmpeg -version | findstr /b "ffmpeg version"
  pause
  exit /b 0
)

where winget >nul 2>nul
if not %errorlevel%==0 (
  echo 未找到 winget。请在系统设置中安装“应用安装程序”，或参考使用说明书手动安装 FFmpeg。
  pause
  exit /b 1
)

echo 即将通过 Windows 软件包管理器安装 FFmpeg Essentials。
winget install --id Gyan.FFmpeg.Essentials --exact --accept-package-agreements --accept-source-agreements
if not %errorlevel%==0 (
  echo 安装失败，请参考使用说明书中的 FFmpeg 故障排查章节。
  pause
  exit /b 1
)
echo 安装完成。请重新启动见舟工作台。
pause
