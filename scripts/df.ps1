#!/usr/bin/env pwsh
#Requires -Version 7
<#
df — comando global do System One Dwarf no Windows (jogo + backend + UI web)

USO:
  .\scripts\df.ps1 [up] [opções]    arranca tudo e fica em primeiro plano (Ctrl-C para parar)
  .\scripts\df.ps1 stop             pára tudo o que 'df up' iniciou (e SÓ isso)
  .\scripts\df.ps1 status           estado de cada componente
  .\scripts\df.ps1 logs [-Follow]   últimos 200 linhas dos logs (-Follow segue)
  .\scripts\df.ps1 install-link     cria df.cmd (+ sod.cmd depreciado) numa pasta do PATH
  .\scripts\df.ps1 help             esta ajuda

OPÇÕES (up):
  --no-game | -NoGame        não arranca o Dwarf Fortress
  --no-backend               não arranca o backend FastAPI
  --no-web                   não arranca a UI web (Vite)
  --port <n>                 porta do backend (predefinição: 8000)
  --web-port <n>             porta da UI web (predefinição: 5173)
  --df-path <dir>            diretório do Dwarf Fortress
                             (predefinição: $env:DF_PATH ou deteção Steam)
  --sound                    aceite por simetria com o df.sh — no Windows o som
                             do jogo fica LIGADO (a regra de silêncio é do Linux)
  -h | --help                esta ajuda

NOTAS WINDOWS:
  • No Windows não existe colisão com o 'df' de disco — o nome é livre.
  • O jogo arranca pelo 'Dwarf Fortress.exe' do diretório do DF; o DFHack é
    carregado pelo próprio jogo (dfhooks.dll + dfhooks_dfhack.ini).
  • Os PID que ESTE comando arranca ficam em .runtime/; 'df stop' mata só esses.
  • A chave OpenRouter é lida sem nunca ser mostrada (SOD_OPENROUTER_KEY /
    OPENROUTER_API_KEY ou ~/.secrets) e só é passada ao processo do backend.

EXEMPLOS:
  .\scripts\df.ps1                        # o mesmo que 'df up'
  .\scripts\df.ps1 up --no-game           # só backend + UI
  .\scripts\df.ps1 up --port 8010 --web-port 5183
  .\scripts\df.ps1 logs -Follow
#>
param([Parameter(ValueFromRemainingArguments = $true)][string[]]$CliArgs)

$ErrorActionPreference = 'Stop'
$Root     = Split-Path -Parent $PSScriptRoot
$Runtime  = Join-Path $Root '.runtime'
$LogsDir  = Join-Path $Runtime 'logs'
$PyExe    = Join-Path $Root '.venv\Scripts\python.exe'
$DefaultPort    = 8000
$DefaultWebPort = 5173

function Say  { param($msg) Write-Host $msg }
function Ok   { param($msg) Write-Host "✅ $msg" }
function Warn { param($msg) Write-Host "⚠️  $msg" }
function Err  { param($msg) [Console]::Error.WriteLine("❌ $msg") }

function Get-SecretValue {          # nunca devolve echo do valor para o ecrã
    param([string[]]$Names)
    foreach ($name in $Names) {
        $envValue = [Environment]::GetEnvironmentVariable($name)
        if ($envValue) { return $envValue }
    }
    $secrets = Join-Path $env:USERPROFILE '.secrets'
    if (Test-Path $secrets) {
        foreach ($line in Get-Content $secrets) {
            foreach ($name in $Names) {
                if ($line.StartsWith("$name=")) {
                    $value = $line.Substring($name.Length + 1).Trim()
                    if ($value.Length -ge 2 -and
                        (($value[0] -eq '"' -and $value[$value.Length - 1] -eq '"') -or
                         ($value[0] -eq "'" -and $value[$value.Length - 1] -eq "'"))) {
                        $value = $value.Substring(1, $value.Length - 2).Trim()
                    }
                    return $value
                }
            }
        }
    }
    return $null
}

function Find-DfPath {
    param([string]$CliPath)
    if ($CliPath) { return $CliPath }
    if ($env:DF_PATH) { return $env:DF_PATH }
    $candidates = @()
    try {
        $steamPath = (Get-ItemProperty 'HKCU:\Software\Valve\Steam' -ErrorAction Stop).SteamPath
        if ($steamPath) { $candidates += (Join-Path $steamPath 'steamapps\common\Dwarf Fortress') }
    } catch { }
    $candidates += 'C:\Program Files (x86)\Steam\steamapps\common\Dwarf Fortress'
    $candidates += 'C:\Program Files\Steam\steamapps\common\Dwarf Fortress'
    foreach ($cand in $candidates) {
        if ((Test-Path $cand) -and (Test-Path (Join-Path $cand 'data'))) { return $cand }
    }
    return $null
}

function Test-PortOpen {
    param([int]$Port)
    $client = [System.Net.Sockets.TcpClient]::new()
    try { return $client.ConnectAsync('127.0.0.1', $Port).Wait(300) } catch { return $false }
    finally { $client.Dispose() }
}

function Test-BackendHealth {
    param([int]$Port)
    try {
        $r = Invoke-WebRequest -Uri "http://127.0.0.1:$Port/api/health" -TimeoutSec 2 -UseBasicParsing
        return ($r.StatusCode -eq 200)
    } catch { return $false }
}

function Start-Owned {
    param([string]$Name, [string]$FilePath, [string[]]$ArgumentList,
          [string]$WorkDir, [hashtable]$ExtraEnv = @{})
    $out = Join-Path $LogsDir "$Name.log"
    $err = Join-Path $LogsDir "$Name.err.log"
    $envBackup = @{}
    foreach ($k in $ExtraEnv.Keys) {
        $envBackup[$k] = [Environment]::GetEnvironmentVariable($k)
        Set-Item -Path "Env:$k" -Value $ExtraEnv[$k]
    }
    try {
        $proc = Start-Process -FilePath $FilePath -ArgumentList $ArgumentList `
                  -WorkingDirectory $WorkDir -RedirectStandardOutput $out `
                  -RedirectStandardError $err -PassThru -WindowStyle Hidden
    } finally {
        foreach ($k in $envBackup.Keys) {
            Set-Item -Path "Env:$k" -Value $envBackup[$k]
        }
    }
    Set-Content -Path (Join-Path $Runtime "$Name.pid") -Value $proc.Id
    return $proc.Id
}

function Stop-OwnedAll {
    $stopped = 0
    foreach ($pidFile in Get-ChildItem -Path $Runtime -Filter '*.pid' -ErrorAction SilentlyContinue) {
        $svcPid = 0
        try { $svcPid = [int](Get-Content $pidFile.FullName -TotalCount 1) } catch { }
        if ($svcPid -gt 0) {
            $proc = Get-Process -Id $svcPid -ErrorAction SilentlyContinue
            if ($proc) {
                Stop-Process -Id $svcPid -Force -ErrorAction SilentlyContinue
                $stopped++
                Say "parado: $($pidFile.BaseName) (pid $svcPid)"
            }
        }
        Remove-Item $pidFile.FullName -Force -ErrorAction SilentlyContinue
    }
    return $stopped
}

function Get-ServiceState {
    param([string]$Name)
    $pidFile = Join-Path $Runtime "$Name.pid"
    if (Test-Path $pidFile) {
        $svcPid = 0
        try { $svcPid = [int](Get-Content $pidFile -TotalCount 1) } catch { }
        if ($svcPid -gt 0 -and (Get-Process -Id $svcPid -ErrorAction SilentlyContinue)) {
            return @{ State = 'a correr'; Pid = $svcPid }
        }
        return @{ State = 'morto (pidfile órfão)'; Pid = $svcPid }
    }
    return @{ State = 'parado'; Pid = 0 }
}

function Show-Help {
    Say "df — comando global do System One Dwarf (jogo + backend + UI web) [Windows]"
    Say ""
    Say "USO:"
    Say "  df.ps1 [up] [opções]    arranca tudo e fica em primeiro plano (Ctrl-C para parar)"
    Say "  df.ps1 stop             pára tudo o que 'df up' iniciou (e SÓ isso)"
    Say "  df.ps1 status           estado de cada componente"
    Say "  df.ps1 logs [-Follow]   últimos 200 linhas dos logs (-Follow segue)"
    Say "  df.ps1 install-link     cria df.cmd (+ sod.cmd depreciado) numa pasta do PATH"
    Say "  df.ps1 help             esta ajuda"
    Say ""
    Say "OPÇÕES (up):"
    Say "  --no-game | --no-backend | --no-web"
    Say "  --port <n>   (8000)   --web-port <n>   (5173)   --df-path <dir>"
    Say "  --sound      aceite por simetria — no Windows o som do jogo fica ligado"
    Say ""
    Say "NOTAS WINDOWS: sem colisão com o 'df' de disco; o DFHack carrega via"
    Say "dfhooks.dll quando o 'Dwarf Fortress.exe' arranca; PIDs próprios em .runtime/."
}

# ------------------------------------------------------------------ comandos
function Cmd-Up {
    param([hashtable]$Options)
    New-Item -ItemType Directory -Force -Path $LogsDir | Out-Null
    $dfPath = Find-DfPath -CliPath $Options.DfPath
    $started = @()

    if (-not $Options.NoBackend) {
        if (Test-PortOpen $Options.Port) { Warn "porta $($Options.Port) já ocupada — backend não arrancado (já corria antes?)" }
        else {
            $key = Get-SecretValue @('SOD_OPENROUTER_KEY', 'OPENROUTER_API_KEY')
            $extra = @{ 'PYTHONPATH' = (Join-Path $Root 'src') }
            if ($key) { $extra['SOD_OPENROUTER_KEY'] = $key }
            $pidBackend = Start-Owned -Name 'backend' -FilePath $PyExe `
                -ArgumentList @('-m', 'uvicorn', 'system_one_dwarf.app.main:app', '--port', "$($Options.Port)") `
                -WorkDir $Root -ExtraEnv $extra
            $started += "backend (pid $pidBackend)"
        }
    }
    if (-not $Options.NoWeb) {
        if (Test-PortOpen $Options.WebPort) { Warn "porta $($Options.WebPort) já ocupada — UI não arrancada" }
        else {
            $envApi = "http://localhost:$($Options.Port)"
            $pidWeb = Start-Owned -Name 'web' -FilePath 'cmd.exe' `
                -ArgumentList @('/c', "set VITE_API_BASE=$envApi&& npm run dev") `
                -WorkDir (Join-Path $Root 'web')
            $started += "web (pid $pidWeb)"
        }
    }
    if (-not $Options.NoGame) {
        if (-not $dfPath) { Err "Dwarf Fortress não encontrado — usa --df-path <dir> ou define DF_PATH" }
        elseif (Get-Process -Name 'Dwarf Fortress' -ErrorAction SilentlyContinue) { Warn "o jogo já está a correr — não arranco outra instância" }
        else {
            $proc = Start-Process -FilePath 'Dwarf Fortress.exe' -WorkingDirectory $dfPath -PassThru
            Set-Content -Path (Join-Path $Runtime 'game.pid') -Value $proc.Id
            $started += "jogo (pid $($proc.Id))"
        }
    }

    foreach ($s in $started) { Ok "arrancado: $s" }
    if ($started.Count -eq 0) { Warn "nada arrancado (tudo já corria ou desativado por flags)" }

    Say ""
    Say "URLs (quando prontos):"
    Say "  backend  → http://localhost:$($Options.Port)   (/api/health)"
    Say "  UI web   → http://localhost:$($Options.WebPort)"
    Say "  RPC      → localhost:5000 (DFHack, com o jogo aberto)"
    Say ""
    Say "Ctrl-C (ou 'df stop') pára só o que este comando arrancou."

    $sigint = $null
    try {
        $sigint = [Console]::add_CancelKeyPress({ param($s, $e) $e.Cancel = $true })
        while ($true) { Start-Sleep -Seconds 2 }   # supervisão em primeiro plano
    } finally {
        if ($sigint) { [Console]::remove_CancelKeyPress($sigint) }
        Say ""
        Say "a parar o que este comando arrancou…"
        Stop-OwnedAll | Out-Null
    }
}

function Cmd-Stop {
    New-Item -ItemType Directory -Force -Path $Runtime | Out-Null
    $owned = (Get-ChildItem -Path $Runtime -Filter '*.pid' -ErrorAction SilentlyContinue).Count
    $stopped = Stop-OwnedAll
    if ($stopped -eq 0 -and $owned -eq 0) { Say "(não havia PID registado por 'df up' neste runtime)" }
    else { Ok "parados $stopped processo(s)" }
}

function Cmd-Status {
    Say "System One Dwarf — estado (Windows)"
    Say ""
    $b = Get-ServiceState 'backend'
    $w = Get-ServiceState 'web'
    $g = Get-ServiceState 'game'
    $backendPort = $DefaultPort; $webPort = $DefaultWebPort
    Say ("  {0,-22} {1,-24} {2}" -f 'backend FastAPI', $b.State, "http://localhost:$backendPort")
    Say ("  {0,-22} {1,-24} {2}" -f 'UI web (Vite)', $w.State, "http://localhost:$webPort")
    Say ("  {0,-22} {1,-24} {2}" -f 'Dwarf Fortress', $g.State, 'RPC localhost:5000')
    Say ""
    Say ("  backend health : {0}" -f $(if (Test-BackendHealth $backendPort) { 'OK' } else { 'sem resposta' }))
    Say ("  porta RPC 5000 : {0}" -f $(if (Test-PortOpen 5000) { 'aberta (jogo com DFHack)' } else { 'fechada (jogo fechado?)' }))
}

function Cmd-Logs {
    param([bool]$Follow)
    foreach ($name in @('backend', 'web', 'game')) {
        $log = Join-Path $LogsDir "$name.log"
        if (Test-Path $log) { Say "===== $name.log ====="; Get-Content $log -Tail 60 }
    }
    if ($Follow) {
        Say "(a seguir os logs — Ctrl-C para sair)"
        Get-Content (Join-Path $LogsDir 'backend.log') -Tail 200 -Wait -ErrorAction SilentlyContinue
    }
}

function Cmd-InstallLink {
    $dir = Join-Path $env:LOCALAPPDATA 'Microsoft\WindowsApps'
    if (-not (Test-Path $dir)) { $dir = Join-Path $env:USERPROFILE 'bin'; New-Item -ItemType Directory -Force -Path $dir | Out-Null }
    $scriptPath = Join-Path $PSScriptRoot 'df.ps1'
    $shim = "@echo off`r`npwsh -NoProfile -ExecutionPolicy Bypass -File `"$scriptPath`" %*`r`n"
    Set-Content -Path (Join-Path $dir 'df.cmd') -Value $shim -Encoding ASCII
    $sodShim = "@echo off`r`necho ⚠ 'sod' foi substituído por 'df' — usa 'df up' 1>&2`r`npwsh -NoProfile -ExecutionPolicy Bypass -File `"$scriptPath`" %*`r`n"
    Set-Content -Path (Join-Path $dir 'sod.cmd') -Value $sodShim -Encoding ASCII
    Ok "criados df.cmd e sod.cmd em $dir"
}

# ------------------------------------------------------------------ despacho
$options = @{
    NoGame = $false; NoBackend = $false; NoWeb = $false
    Port = $DefaultPort; WebPort = $DefaultWebPort; DfPath = $null
}
$command = 'up'
$follow = $false
$argsList = @($CliArgs)
for ($i = 0; $i -lt $argsList.Count; $i++) {
    $a = $argsList[$i]
    switch -Regex ($a) {
        '^(up|stop|status|logs|help|install-link)$' { if ($i -eq 0) { $command = $a } }
        '^(-h|--help)$' { $command = 'help' }
        '^(--no-game|-NoGame)$' { $options.NoGame = $true }
        '^--no-backend$' { $options.NoBackend = $true }
        '^--no-web$' { $options.NoWeb = $true }
        '^(--sound)$' { }   # no Windows o som fica ligado; aceite por simetria
        '^--port$' { $options.Port = [int]$argsList[++$i] }
        '^--web-port$' { $options.WebPort = [int]$argsList[++$i] }
        '^--df-path$' { $options.DfPath = $argsList[++$i] }
        '^(--follow|-f)$' { $follow = $true }
        default { Err "argumento desconhecido: $a (vê: df.ps1 help)"; exit 2 }
    }
}

switch ($command) {
    'up'          { Cmd-Up -Options $options }
    'stop'        { Cmd-Stop }
    'status'      { Cmd-Status }
    'logs'        { Cmd-Logs -Follow $follow }
    'install-link'{ Cmd-InstallLink }
    'help'        { Show-Help }
}
