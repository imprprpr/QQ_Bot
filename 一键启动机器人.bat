@echo off
cd /d %~dp0
title QQ Bot Server (NoneBot)
color 0B

echo ==================================================
echo.
echo    Starting QQ Bot Server (python bot.py)...
echo.
echo    Please keep this window OPEN!
echo.
echo ==================================================
echo.

python bot.py

echo.
echo ==================================================
echo Bot process stopped!
echo ==================================================
pause
