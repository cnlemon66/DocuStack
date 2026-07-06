@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ============================================
echo   MyBatis RAG Java 后端
echo ============================================
echo.
echo 编译中...
call mvn clean package -DskipTests -q
if %errorlevel% neq 0 (
    echo 编译失败！
    pause
    exit /b 1
)
echo 编译成功，启动服务...
echo.
start "" http://localhost:8766
java -jar target/mybatis-rag-1.0.0.jar
pause
