@echo off
chcp 65001 >nul
cd /d "%~dp0"
title DocuStack

echo.
echo   ╔═════════════════════════════════╗
echo   ║        DocuStack               ║
echo   ║  Java 技术文档智能问答系统       ║
echo   ╚═════════════════════════════════╝
echo.

:: 检查 config.json
if not exist "config.json" (
    echo [⚠]  未找到 config.json，正在从模板创建...
    copy "config.example.json" "config.json" >nul
)

:: 检查 API Key
python -c "import json;c=json.load(open('config.json','r',encoding='utf-8'));k=c.get('llm',{}).get('api_key','');exit(0 if k and '你的' not in k and len(k)>10 else 1)" >nul 2>&1
if errorlevel 1 (
    echo.
    echo   ┌────────────────────────────────────────┐
    echo   │  需要配置 DeepSeek API Key               │
    echo   │  获取地址: https://platform.deepseek.com  │
    echo   └────────────────────────────────────────┘
    echo.
    set /p APIKEY="  请输入 DeepSeek API Key: "
    if "%APIKEY%"=="" (
        echo   [✗] 未输入 Key，退出
        pause
        exit
    )
    python scripts/set_key.py "%APIKEY%"
    if errorlevel 1 (
        echo   [✗] 保存失败
        pause
        exit
    )
    echo   [✓] Key 已保存
) else (
    echo   [✓] API Key 已配置
)

:: 检查索引
if not exist "data\vector_db\index.json" (
    echo.
    echo   [⚠]  未找到向量索引，构建约需 5-10 分钟
    choice /c yn /m "  是否现在构建？"
    if errorlevel 2 goto :skip_index
    echo   正在构建...
    python index.py
:skip_index
    echo.
)

echo   [✓] 正在启动服务...
echo   打开浏览器访问: http://localhost:8765
echo.
timeout /t 2 >nul
start "" http://localhost:8765
python server.py
pause
