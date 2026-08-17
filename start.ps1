<#
    Stock 项目启动脚本（Windows PowerShell 5.1+）
    用法：
        .\start.ps1            # 启动后端(8000) + 前端(8082)
        .\start.ps1 start
        .\start.ps1 stop
        .\start.ps1 status
        .\start.ps1 restart
#>
param([Parameter(Position = 0)][string]$Action = "start")

$ErrorActionPreference = 'Stop'
try {
    [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
    $OutputEncoding = [System.Text.Encoding]::UTF8
} catch {}

$ROOT = Split-Path -Parent $MyInvocation.MyCommand.Path
$RUN_DIR = Join-Path $ROOT ".run"
$BACKEND_DIR = Join-Path $ROOT "backend"
$FRONTEND_DIR = Join-Path $ROOT "frontend"
$BACKEND_PORT = 8000
$FRONTEND_PORT = 8082
$BACKEND_LOG = Join-Path $RUN_DIR "backend.log"
$BACKEND_ERR = Join-Path $RUN_DIR "backend.err"
$FRONTEND_LOG = Join-Path $RUN_DIR "frontend.log"
$FRONTEND_ERR = Join-Path $RUN_DIR "frontend.err"
$BACKEND_PID = Join-Path $RUN_DIR "backend.pid"
$FRONTEND_PID = Join-Path $RUN_DIR "frontend.pid"

New-Item -ItemType Directory -Force -Path $RUN_DIR | Out-Null

# 优先使用后端虚拟环境里的 Python
$py = Join-Path $BACKEND_DIR ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) {
    $cmd = Get-Command python.exe -ErrorAction SilentlyContinue
    if (-not $cmd) { $cmd = Get-Command py.exe -ErrorAction SilentlyContinue }
    if ($cmd) { $py = $cmd.Source }
}

$node = (Get-Command node.exe -ErrorAction SilentlyContinue).Source

function Test-PortListening {
    param([int]$Port)
    $match = netstat -ano 2>&1 | Select-String (":$Port\s") | Select-String "LISTENING"
    return ($null -ne $match)
}

function Start-Backend {
    if (Test-PortListening $BACKEND_PORT) { Write-Host "[start] 后端已在运行 :$BACKEND_PORT"; return }
    if (-not $py -or -not (Test-Path $py)) { Write-Host "[start] 找不到 Python，请先运行 .\setup.ps1"; return }
    Write-Host "[start] 启动后端 :$BACKEND_PORT ..."
    $args = @("-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "$BACKEND_PORT")
    $p = Start-Process -FilePath $py -ArgumentList $args -WorkingDirectory $BACKEND_DIR `
        -RedirectStandardOutput $BACKEND_LOG -RedirectStandardError $BACKEND_ERR `
        -WindowStyle Hidden -PassThru
    $p.Id | Set-Content $BACKEND_PID
    Write-Host "[start] 后端 PID=$($p.Id)"
}

function Start-Frontend {
    if (Test-PortListening $FRONTEND_PORT) { Write-Host "[start] 前端已在运行 :$FRONTEND_PORT"; return }
    $vite = Join-Path $FRONTEND_DIR "node_modules\vite\bin\vite.js"
    if (-not (Test-Path $vite)) { Write-Host "[start] 找不到 vite，请先运行 .\setup.ps1"; return }
    if (-not $node) { Write-Host "[start] 找不到 Node.js，请先安装 Node 18+"; return }
    Write-Host "[start] 启动前端 :$FRONTEND_PORT ..."
    $p = Start-Process -FilePath $node -ArgumentList @($vite) -WorkingDirectory $FRONTEND_DIR `
        -RedirectStandardOutput $FRONTEND_LOG -RedirectStandardError $FRONTEND_ERR `
        -WindowStyle Hidden -PassThru
    $p.Id | Set-Content $FRONTEND_PID
    Write-Host "[start] 前端 PID=$($p.Id)"
}

function Stop-All {
    foreach ($f in @($FRONTEND_PID, $BACKEND_PID)) {
        if (Test-Path $f) {
            $pidVal = Get-Content $f -ErrorAction SilentlyContinue
            if ($pidVal) {
                try { Stop-Process -Id ([int]$pidVal) -Force -ErrorAction Stop; Write-Host "[stop] 已停止 PID $pidVal" } catch {}
            }
            Remove-Item $f -ErrorAction SilentlyContinue
        }
    }
    # 端口兜底
    foreach ($port in @($FRONTEND_PORT, $BACKEND_PORT)) {
        if (Test-PortListening $port) {
            $line = netstat -ano 2>&1 | Select-String (":$port\s") | Select-String "LISTENING" | Select-Object -First 1
            if ($line) {
                $parts = $line.ToString().Trim() -split '\s+'
                $procId = $parts[-1]
                if ($procId -match '^\d+$') {
                    try { Stop-Process -Id ([int]$procId) -Force -ErrorAction Stop; Write-Host "[stop] 按端口 $port 停止 PID $procId" } catch {}
                }
            }
        }
    }
    Write-Host "[stop] 完成"
}

function Show-Status {
    if (Test-PortListening $BACKEND_PORT) { Write-Host "后端  :$BACKEND_PORT  运行中" -ForegroundColor Green } else { Write-Host "后端  :$BACKEND_PORT  未运行" -ForegroundColor Red }
    if (Test-PortListening $FRONTEND_PORT) { Write-Host "前端  :$FRONTEND_PORT  运行中" -ForegroundColor Green } else { Write-Host "前端  :$FRONTEND_PORT  未运行" -ForegroundColor Red }
}

switch ($Action) {
    'start'   { Start-Backend; Start-Frontend }
    'stop'    { Stop-All }
    'status'  { Show-Status }
    'restart' { Stop-All; Start-Sleep -Seconds 1; Start-Backend; Start-Frontend }
    default   { Write-Host "用法: .\start.ps1 {start|stop|status|restart}"; exit 1 }
}
