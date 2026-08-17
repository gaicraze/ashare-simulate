<#
    Stock 一键部署（Windows PowerShell 5.1+）
    自动检测环境 → 创建虚拟环境并安装依赖 → 重建数据湖 → 启动服务。

    用法：
        powershell -ExecutionPolicy Bypass -File .\setup.ps1
        powershell -ExecutionPolicy Bypass -File .\setup.ps1 -SkipData
#>
param(
    [switch]$SkipBackend,
    [switch]$SkipFrontend,
    [switch]$SkipData
)

$ErrorActionPreference = 'Stop'
try {
    [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
    $OutputEncoding = [System.Text.Encoding]::UTF8
} catch {}

$ROOT = Split-Path -Parent $MyInvocation.MyCommand.Path

# 找一个可用的 Python（bootstrap 内部会再精确校验版本）
$py = $null
foreach ($c in @('python.exe', 'py.exe', 'python3.exe')) {
    $cmd = Get-Command $c -ErrorAction SilentlyContinue
    if ($cmd) { $py = $cmd.Source; break }
}
if (-not $py) {
    Write-Host "[x] 未找到 Python。请先安装 Python 3.11+：https://www.python.org/downloads/" -ForegroundColor Red
    exit 1
}

Write-Host "===== 开始一键部署 ====="
$bootstrapArgs = @()
if ($SkipBackend)  { $bootstrapArgs += '--skip-backend' }
if ($SkipFrontend) { $bootstrapArgs += '--skip-frontend' }
if ($SkipData)     { $bootstrapArgs += '--skip-data' }

& $py (Join-Path $ROOT "scripts\bootstrap.py") @bootstrapArgs
if ($LASTEXITCODE -ne 0) {
    Write-Host "[x] 部署失败，请检查上方输出。" -ForegroundColor Red
    exit $LASTEXITCODE
}

Write-Host ""
Write-Host "===== 启动服务 ====="
& (Join-Path $ROOT "start.ps1") start

Write-Host ""
Write-Host "部署完成。浏览器访问：http://127.0.0.1:8082"
Write-Host "常用命令："
Write-Host "  .\start.ps1 status"
Write-Host "  .\start.ps1 stop"
Write-Host "  .\start.ps1 restart"
