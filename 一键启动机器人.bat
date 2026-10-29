@echo off
chcp 65001 >nul
setlocal EnableExtensions
cd /d "%~dp0"
title AnimaBot

rem ===== 1. 找到 Python 3.10 或更高版本 =====
set "PY="
where py >nul 2>nul && py -3 -c "import sys; sys.exit(sys.version_info < (3, 10))" >nul 2>nul && set "PY=py -3"
if not defined PY (
  where python >nul 2>nul && python -c "import sys; sys.exit(sys.version_info < (3, 10))" >nul 2>nul && set "PY=python"
)
if not defined PY (
  echo [!] 没有找到 Python 3.10 或更高版本。
  echo     正在打开 Python 下载页。安装时请勾选 "Add python.exe to PATH"，装好后重新双击本文件。
  start "" https://www.python.org/downloads/
  pause
  exit /b 1
)

rem ===== 2. 独立运行环境（只在第一次创建）=====
if not exist ".venv\Scripts\python.exe" (
  echo 正在创建运行环境，只需一次...
  %PY% -m venv .venv
  if errorlevel 1 (
    echo [!] 创建运行环境失败，请确认 Python 安装完整。
    pause
    exit /b 1
  )
)
set "VPY=.venv\Scripts\python.exe"

rem ===== 3. 安装依赖（requirements.txt 变化时才重新安装）=====
fc /b requirements.txt .venv\requirements.installed >nul 2>nul
if errorlevel 1 (
  echo 正在安装依赖，第一次大约需要 1 到 3 分钟...
  "%VPY%" -m pip install --disable-pip-version-check -q -r requirements.txt
  if errorlevel 1 (
    echo 默认下载源失败，改用国内镜像重试...
    "%VPY%" -m pip install --disable-pip-version-check -q -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
    if errorlevel 1 (
      echo [!] 依赖安装失败，请检查网络后重新双击本文件。
      pause
      exit /b 1
    )
  )
  copy /y requirements.txt .venv\requirements.installed >nul
)

rem ===== 4. 第一次使用：配置向导 =====
"%VPY%" tools\setup_wizard.py --check
if errorlevel 1 (
  "%VPY%" tools\setup_wizard.py
  if errorlevel 1 (
    pause
    exit /b 1
  )
)

rem ===== 5. 启动 =====
echo.
echo ==================================================
echo   AnimaBot 正在运行。关闭这个窗口就会停止机器人。
echo   管理页面令牌：另开窗口运行  .venv\Scripts\python.exe tools\manage.py
echo   重新配置模型：另开窗口运行  .venv\Scripts\python.exe tools\setup_wizard.py
echo ==================================================
echo.
"%VPY%" bot.py
echo.
echo 机器人已停止。如果是意外退出，请把上面的报错截图发给维护者。
pause
