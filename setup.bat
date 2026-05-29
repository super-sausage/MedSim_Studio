@echo off
chcp 65001 >nul
title CT Simulator - Docker Setup
setlocal enabledelayedexpansion

:: ============================================================
:: CT Simulator - Docker 一键启动脚本 (Windows)
:: ============================================================

set SCRIPT_DIR=%~dp0
cd /d "%SCRIPT_DIR%"

:: ---- 检查 Docker ----
echo [1/4] 检查 Docker 环境...
docker info >nul 2>&1
if %errorlevel% neq 0 (
    echo [错误] Docker Desktop 未运行或未安装。
    echo   请先安装 Docker Desktop: https://www.docker.com/products/docker-desktop/
    echo   安装完成后启动 Docker Desktop，然后重新运行此脚本。
    pause
    exit /b 1
)
echo    Docker 运行正常

:: ---- 创建 .env 文件（如不存在）----
echo [2/4] 检查环境配置...
if not exist ".env" (
    if exist ".env.example" (
        copy ".env.example" ".env" >nul
        echo    已从 .env.example 生成 .env 文件
    ) else (
        echo [错误] 找不到 .env.example 文件
        pause
        exit /b 1
    )
) else (
    echo     .env 文件已存在，跳过
)

:: ---- 创建所需的本地目录 ----
echo [3/4] 创建运行时目录...
if not exist ".\docker\postgres" mkdir ".\docker\postgres" 2>nul

:: ---- 启动 Docker 容器 ----
echo [4/4] 构建并启动 Docker 容器...
echo    首次启动需要下载镜像并安装依赖，可能需要 5-10 分钟，请耐心等待...
echo.

docker compose up -d --build

if %errorlevel% neq 0 (
    echo.
    echo [错误] Docker 容器启动失败，请检查上方日志。
    pause
    exit /b 1
)

echo.
echo ============================================================
echo  启动成功！
echo ============================================================
echo.
echo  前端页面:    http://localhost:5173
echo  API 文档:    http://localhost:8000/docs
echo  健康检查:    http://localhost:8000/health
echo  MinIO 控制台: http://localhost:9001  (admin / minioadmin123)
echo.
echo  查看容器状态: docker compose ps
echo  查看后端日志: docker compose logs -f backend
echo  停止容器:     docker compose down
echo.
echo ============================================================

pause
