<#
.SYNOPSIS
    mindkeep 一键安装脚本 (Windows)

.DESCRIPTION
    检查 Python (>=3.9)，按需安装 pipx，安装 mindkeep，自动把 scripts 目录加入 PATH，
    并运行 `mindkeep doctor` 输出体检报告。

.PARAMETER Source
    安装源。默认 git+https://github.com/AllenS0104/mindkeep.git；
    也接受本地 wheel 路径或 PyPI 包名 (如 mindkeep==0.2.0)。

.PARAMETER Method
    pipx | pip | auto (默认)。auto 优先用 pipx，否则 pip --user。

.PARAMETER Upgrade
    若已安装则升级。

.PARAMETER Quiet
    静默模式（减少输出）。

.EXAMPLE
    iwr https://raw.githubusercontent.com/AllenS0104/mindkeep/main/install.ps1 | iex

.EXAMPLE
    .\install.ps1 -Source .\dist\mindkeep-0.2.0-py3-none-any.whl -Method pip

.EXAMPLE
    .\install.ps1 -Upgrade
#>
[CmdletBinding()]
param(
    [string]$Source = "git+https://github.com/AllenS0104/mindkeep.git",
    [ValidateSet("pipx", "pip", "auto")]
    [string]$Method = "auto",
    [switch]$Upgrade,
    [switch]$Quiet
)

$ErrorActionPreference = "Stop"

# ─── 输出辅助 ────────────────────────────────────────────────────
function Write-Step {
    param([int]$N, [string]$Msg)
    if (-not $Quiet) { Write-Host "`n[$N] $Msg" -ForegroundColor Cyan }
}
function Write-Ok    { param([string]$m) if (-not $Quiet) { Write-Host "  ✅ $m" -ForegroundColor Green } }
function Write-Warn2 { param([string]$m) Write-Host "  ⚠️  $m" -ForegroundColor Yellow }
function Write-Err2  { param([string]$m) Write-Host "  ❌ $m" -ForegroundColor Red }
function Write-Info  { param([string]$m) if (-not $Quiet) { Write-Host "  $m" -ForegroundColor Gray } }

if (-not $Quiet) {
    Write-Host "╭──────────────────────────────────────────╮" -ForegroundColor Magenta
    Write-Host "│  mindkeep installer (Windows)        │" -ForegroundColor Magenta
    Write-Host "╰──────────────────────────────────────────╯" -ForegroundColor Magenta
    Write-Host "Source : $Source" -ForegroundColor DarkGray
    Write-Host "Method : $Method" -ForegroundColor DarkGray
}

# ─── 1. 检查 Python ───────────────────────────────────────────────
Write-Step 1 "检查 Python (>=3.9)"

$pythonExe = $null
foreach ($candidate in @("py", "python", "python3")) {
    $cmd = Get-Command $candidate -ErrorAction SilentlyContinue
    if (-not $cmd) { continue }
    try {
        if ($candidate -eq "py") {
            $ver = & $candidate -3 --version 2>&1
            if ($LASTEXITCODE -eq 0) { $pythonExe = @("py", "-3"); break }
        } else {
            $ver = & $candidate --version 2>&1
            if ($LASTEXITCODE -eq 0) { $pythonExe = @($candidate); break }
        }
    } catch {}
}

if (-not $pythonExe) {
    Write-Err2 "未检测到 Python。请先安装 Python 3.9+："
    Write-Host "    https://www.python.org/downloads/" -ForegroundColor Yellow
    Write-Host "    安装时勾选 'Add Python to PATH'" -ForegroundColor Yellow
    exit 1
}

# 校验版本 >= 3.9
$verOutput = & $pythonExe[0] $pythonExe[1..($pythonExe.Length-1)] -c "import sys; print('{0}.{1}'.format(sys.version_info[0], sys.version_info[1]))" 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Err2 "无法执行 Python: $verOutput"
    exit 1
}
$verParts = $verOutput.Trim().Split('.')
$major = [int]$verParts[0]; $minor = [int]$verParts[1]
if ($major -lt 3 -or ($major -eq 3 -and $minor -lt 9)) {
    Write-Err2 "Python 版本过低 ($verOutput)，需要 >= 3.9"
    Write-Host "    请升级：https://www.python.org/downloads/" -ForegroundColor Yellow
    exit 1
}
Write-Ok "Python $verOutput ($($pythonExe -join ' '))"

# 便捷调用器
function Invoke-Py {
    $argsList = @()
    $argsList += $pythonExe[1..($pythonExe.Length-1)]
    $argsList += $args
    & $pythonExe[0] @argsList
}

# ─── 2. 决定 method ──────────────────────────────────────────────
Write-Step 2 "决定安装方式"

$resolvedMethod = $Method
if ($resolvedMethod -eq "auto") {
    Invoke-Py -m pipx --version *> $null
    if ($LASTEXITCODE -eq 0) {
        $resolvedMethod = "pipx"
        Write-Ok "检测到 pipx，使用 pipx 安装"
    } else {
        Write-Info "未检测到 pipx，尝试安装 pipx..."
        Invoke-Py -m pip install --user --quiet pipx
        if ($LASTEXITCODE -eq 0) {
            Invoke-Py -m pipx ensurepath *> $null
            Invoke-Py -m pipx --version *> $null
            if ($LASTEXITCODE -eq 0) {
                $resolvedMethod = "pipx"
                Write-Ok "pipx 安装成功"
            } else {
                $resolvedMethod = "pip"
                Write-Warn2 "pipx 安装后仍不可用，回退到 pip --user"
            }
        } else {
            $resolvedMethod = "pip"
            Write-Warn2 "pipx 安装失败，回退到 pip --user"
        }
    }
} elseif ($resolvedMethod -eq "pipx") {
    Invoke-Py -m pipx --version *> $null
    if ($LASTEXITCODE -ne 0) {
        Write-Info "pipx 未安装，正在安装..."
        Invoke-Py -m pip install --user pipx
        if ($LASTEXITCODE -ne 0) { Write-Err2 "pipx 安装失败"; exit 1 }
        Invoke-Py -m pipx ensurepath *> $null
    }
    Write-Ok "使用 pipx"
} else {
    Write-Ok "使用 pip --user"
}

# ─── 3. 安装 mindkeep ─────────────────────────────────────────
Write-Step 3 "安装 mindkeep"
Write-Info "来源: $Source"

if ($resolvedMethod -eq "pipx") {
    # 检查是否已装（pipx 在无任何 app 时会往 stderr 输出提示，
    # PowerShell 合流 stderr 时会触发 NativeCommandError；
    # 这里抑制 stderr 并显式检查退出码，避免首装崩溃）
    $prevEAP = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    $pipxList = Invoke-Py -m pipx list --short 2>$null
    $ErrorActionPreference = $prevEAP
    if ($LASTEXITCODE -ne 0 -or -not $pipxList) {
        $pipxList = ""
    }
    $alreadyInstalled = $pipxList -match "mindkeep"
    if ($alreadyInstalled) {
        if ($Upgrade) {
            Write-Info "已安装，执行 upgrade..."
            # pipx upgrade 对 git+ 源需用 reinstall 保证 Source 生效
            if ($Source -match "^(git\+|https?://|\.?[\\/]|[A-Za-z]:[\\/])" -or $Source -like "*.whl") {
                Invoke-Py -m pipx install --force $Source
            } else {
                Invoke-Py -m pipx upgrade mindkeep
            }
        } else {
            Write-Warn2 "已安装 mindkeep。使用 -Upgrade 强制升级，或跳过安装。"
        }
    } else {
        Invoke-Py -m pipx install $Source
    }
} else {
    $pipArgs = @("-m", "pip", "install", "--user")
    if ($Upgrade) { $pipArgs += "--upgrade" }
    $pipArgs += $Source
    Invoke-Py @pipArgs
}

if ($LASTEXITCODE -ne 0) {
    Write-Err2 "安装失败 (exit=$LASTEXITCODE)"
    Write-Host "    建议：" -ForegroundColor Yellow
    Write-Host "      • 检查网络与 Source 是否正确" -ForegroundColor Yellow
    Write-Host "      • 尝试: $($pythonExe -join ' ') -m pip install --upgrade pip" -ForegroundColor Yellow
    Write-Host "      • 或改用 -Method pip" -ForegroundColor Yellow
    exit 1
}
Write-Ok "安装完成"

# ─── 4. 加入 PATH ─────────────────────────────────────────────────
Write-Step 4 "确保 scripts 目录在 PATH"

$scriptsDirs = @()

# 用户 scripts (pip --user)
$userScripts = (Invoke-Py -c "import sysconfig; print(sysconfig.get_path('scripts','nt_user'))" 2>&1).Trim()
if ($LASTEXITCODE -eq 0 -and $userScripts) {
    $scriptsDirs += $userScripts
}

# pipx bin 目录
if ($resolvedMethod -eq "pipx") {
    $pipxBin = Join-Path $env:USERPROFILE ".local\bin"
    $scriptsDirs += $pipxBin
}

$userPath = [Environment]::GetEnvironmentVariable("Path", "User")
if (-not $userPath) { $userPath = "" }
$pathChanged = $false

foreach ($d in ($scriptsDirs | Select-Object -Unique)) {
    if (-not (Test-Path $d)) { continue }
    $inCurrent = ($env:Path -split ';') -contains $d
    $inUser    = ($userPath -split ';') -contains $d
    if (-not $inCurrent) { $env:Path += ";$d" }
    if (-not $inUser) {
        $userPath = if ($userPath) { "$userPath;$d" } else { $d }
        $pathChanged = $true
        Write-Ok "已添加到用户 PATH: $d"
    } else {
        Write-Info "已在 PATH: $d"
    }
}

if ($pathChanged) {
    [Environment]::SetEnvironmentVariable("Path", $userPath, "User")
    Write-Warn2 "新开 PowerShell 终端后 mindkeep 命令可直接调用"
}

# ─── 5. 验证 ──────────────────────────────────────────────────────
Write-Step 5 "验证安装"

$amCmd = Get-Command mindkeep -ErrorAction SilentlyContinue
$verOk = $false
if ($amCmd) {
    $v = & mindkeep --version 2>&1
    if ($LASTEXITCODE -eq 0) {
        Write-Ok "mindkeep --version → $v"
        $verOk = $true
    }
}
if (-not $verOk) {
    $v = Invoke-Py -m mindkeep --version 2>&1
    if ($LASTEXITCODE -eq 0) {
        Write-Ok "python -m mindkeep --version → $v"
        $verOk = $true
    } else {
        Write-Warn2 "无法调用 mindkeep (可能需要重开终端使 PATH 生效)"
    }
}

Write-Step 6 "运行 doctor 体检"
if ($amCmd) {
    & mindkeep doctor
} else {
    Invoke-Py -m mindkeep doctor
}

if (-not $Quiet) {
    Write-Host "`n🎉 完成！" -ForegroundColor Green
    if ($pathChanged) {
        Write-Host "   提示：如命令仍提示找不到，请重开 PowerShell 终端。" -ForegroundColor Yellow
    }
}
