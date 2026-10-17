@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion
title 下载识别模型（X-ASR 480ms）
cd /d "%~dp0"

set "DEST=%~dp0models\x-asr-480ms"
set "BASE=https://hf-mirror.com/GilgameshWind/X-ASR-zh-en/resolve/main/deployment/models/chunk-480ms-model"

echo ============================================================
echo  下载 X-ASR 中英混说识别模型（自带标点）
echo  目标目录: %DEST%
echo  总大小约 600MB，支持断点续传（重跑本脚本即可）
echo ============================================================
echo.

where curl >nul 2>nul
if errorlevel 1 (
  echo [错误] 系统没有 curl.exe（Windows 10 1803 以上自带）。
  echo 请手动用浏览器下载下面 4 个文件，放进 %DEST%
  echo   %BASE%/encoder-480ms.onnx
  echo   %BASE%/decoder-480ms.onnx
  echo   %BASE%/joiner-480ms.onnx
  echo   %BASE%/tokens.txt
  pause
  exit /b 1
)

if not exist "%DEST%" mkdir "%DEST%"

for %%F in (encoder-480ms.onnx decoder-480ms.onnx joiner-480ms.onnx tokens.txt) do (
  if exist "%DEST%\%%F" (
    echo [跳过] %%F 已存在
  ) else (
    echo [下载] %%F ...
    curl -L --retry 3 -C - -o "%DEST%\%%F" "%BASE%/%%F"
    if errorlevel 1 (
      echo [失败] %%F 下载中断，重新运行本脚本可续传
      pause
      exit /b 1
    )
  )
)

echo.
echo 完成。模型已放到 %DEST%
echo 双击 SubtitleTranslator.exe 启动，状态栏会显示 x-asr-480ms。
pause
