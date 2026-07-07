@echo off
chcp 65001 >nul 2>&1
setlocal enabledelayedexpansion
cd /d "%~dp0"
title DocuStack

echo.
echo   ========================================
echo         DocuStack - Java Doc AI Q&A
echo   ========================================
echo.

:: check config
if not exist "config.json" (
    echo [!] config.json not found, creating from template...
    copy "config.example.json" "config.json" >nul
)

:: check API key
python -c "import json;c=json.load(open('config.json','r',encoding='utf-8'));k=c.get('llm',{}).get('api_key','');exit(0 if k and k.startswith('sk-') and '\u4f60\u7684' not in k and len(k)>10 else 1)" >nul 2>&1
if errorlevel 1 (
    echo.
    echo   +--------------------------------------+
    echo   ^|  API Key required                    ^|
    echo   ^|  Supports DeepSeek/OpenAI/Zhipu/...  ^|
    echo   +--------------------------------------+
    echo.
    set /p APIKEY="  Enter API Key: "
    if "!APIKEY!"=="" (
        echo   [X] No key, exit
        pause
        exit
    )
    python scripts\set_key.py "!APIKEY!"
    if errorlevel 1 (
        echo   [X] Failed to save key
        pause
        exit
    )
    echo   [OK] Key saved
) else (
    echo   [OK] API key configured
)

:: check index
if not exist "data\vector_db\index.json" (
    echo.
    echo   [!] No index found. Build now? (Y/N)
    choice /c yn /m "  "
    if errorlevel 2 goto :skip_index
    echo   Building index...
    python index.py
:skip_index
    echo.
)

echo   [OK] Starting server on http://localhost:8765
echo.
timeout /t 2 >nul
start "" http://localhost:8765
python server.py
pause
