# AI novel platform launcher
$ErrorActionPreference = "SilentlyContinue"
$port = 8788
$url  = "http://127.0.0.1:$port"
$py   = "C:\Users\10667\.workbuddy\binaries\python\envs\default\Scripts\python.exe"
$dir  = "D:\ai-xiaoshuo"
$log  = Join-Path $dir "data\launcher.log"

function Write-Log($msg) {
    try { Add-Content -Path $log -Value ("[" + (Get-Date -Format "yyyy-MM-dd HH:mm:ss") + "] " + $msg) } catch {}
}

function Test-Port {
    try {
        $c = New-Object System.Net.Sockets.TcpClient
        $c.Connect("127.0.0.1", $port)
        $ok = $c.Connected
        $c.Close()
        return $ok
    } catch {
        return $false
    }
}

if (Test-Port) {
    Write-Log "already running, open browser"
    Start-Process $url
    exit
}

Write-Log "starting service"
Start-Process -FilePath $py -ArgumentList @("-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "$port") -WorkingDirectory $dir -WindowStyle Hidden

$ok = $false
for ($i = 0; $i -lt 90; $i++) {
    Start-Sleep -Milliseconds 1000
    if (Test-Port) { $ok = $true; break }
}
Write-Log ("start " + $(if ($ok) { "OK" } else { "TIMEOUT" }))
if ($ok) { Start-Process $url }
