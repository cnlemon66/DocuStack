@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ============================================
echo   MyBatis RAG Web 服务
echo ============================================
echo.
start "" http://localhost:8765
echo 正在启动服务器...
echo.
python server.py
pause
