param(
    [Parameter(Mandatory=$true)]
    [string]$Version,

    [switch]$Beta,

    [switch]$ModLoader
)

Add-Type -AssemblyName System.IO.Compression.FileSystem

# Корень проекта
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path

if ($ModLoader) {
    if ($Beta) {
        $FinalName = "DW2Russian_Beta_${Version}_ModLoader"
    } else {
        $FinalName = "DW2Russian_${Version}_ModLoader"
    }

    $BuildRoot = Join-Path $ProjectRoot "build"
    $ModPath = Join-Path $BuildRoot $FinalName
    $ZipPath = Join-Path $BuildRoot ($FinalName + ".zip")

    if (-not (Test-Path $BuildRoot)) {
        New-Item -ItemType Directory -Path $BuildRoot | Out-Null
    }
    if (Test-Path $ModPath) {
        Remove-Item $ModPath -Recurse -Force
    }
    if (Test-Path $ZipPath) {
        Remove-Item $ZipPath -Force
    }

    $PythonExecutable = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
    if (-not (Test-Path $PythonExecutable)) {
        $PythonExecutable = (Get-Command python -ErrorAction Stop).Source
    }
    Push-Location $ProjectRoot
    try {
        & $PythonExecutable "batch_translate.py" `
            "--target-version" $Version `
            "--export-modloader" $ModPath
        if ($LASTEXITCODE -ne 0) {
            throw "Экспорт перевода для DW2 Mod Launcher завершился с ошибкой."
        }
    }
    finally {
        Pop-Location
    }

    $compression = [System.IO.Compression.CompressionLevel]::NoCompression
    [System.IO.Compression.ZipFile]::CreateFromDirectory($ModPath, $ZipPath, $compression, $false)

    Write-Host "Папка мода создана: $ModPath"
    Write-Host "ZIP создан: $ZipPath"
    return
}

# Имя итогового файла
if ($Beta) {
    $FinalName = "DW2Russian_Beta_${Version}_full"
} else {
    $FinalName = "DW2Russian_${Version}_full"
}

# Временная директория сборки
$BuildPath = Join-Path $ProjectRoot "_build_tmp_${Version}"

# Если осталась от прошлого запуска — удаляем
if (Test-Path $BuildPath) {
    Remove-Item $BuildPath -Recurse -Force
}
New-Item -ItemType Directory -Path $BuildPath | Out-Null

# Источники (версия подставляется автоматически)
$SourceDirs = @(
    "$Version\Russian\DLC Atuuk and Wekkarus\",
    "$Version\Russian\DLC Ikkuro and Dhayut\",
    "$Version\Russian\DLC Quameno and Gizureans\",
    "$Version\Russian\DLC Return of the Shakturi\",
    "$Version\Russian\DLC Shadows Rising\",
    "$Version\Russian\DW2\",
    "data"
)

# Копирование
foreach ($rel in $SourceDirs) {
    $src = Join-Path $ProjectRoot $rel

    if (-not (Test-Path $src)) {
        Write-Host "Не найдено: $rel"
        continue
    }

    Copy-Item -Path (Join-Path $src "*") -Destination $BuildPath -Recurse -Force
}

# --- mod.json ---
$ModJsonSrc = Join-Path $ProjectRoot "mod.json"
$ModJsonDst = Join-Path $BuildPath "mod.json"

if (Test-Path $ModJsonSrc) {
    $json = Get-Content $ModJsonSrc -Raw | ConvertFrom-Json
    $json.version = $Version
    $json | ConvertTo-Json -Depth 10 | Set-Content $ModJsonDst -Encoding UTF8
    Write-Host "mod.json обновлён и скопирован"
}
else {
    Write-Host "mod.json не найден!"
}

# --- steam-thumb.jpg ---
$ThumbSrc = Join-Path $ProjectRoot "steam-thumb.jpg"
$ThumbDst = Join-Path $BuildPath "steam-thumb.jpg"

if (Test-Path $ThumbSrc) {
    Copy-Item -Path $ThumbSrc -Destination $ThumbDst -Force
    Write-Host "steam-thumb.jpg скопирован"
}
else {
    Write-Host "steam-thumb.jpg не найден!"
}

# Папка releases
$ReleasesPath = Join-Path $ProjectRoot "releases"
if (-not (Test-Path $ReleasesPath)) {
    New-Item -ItemType Directory -Path $ReleasesPath | Out-Null
}

# Путь к ZIP
$ZipPath = Join-Path $ReleasesPath ($FinalName + ".zip")

# Если ZIP уже существует — удаляем
if (Test-Path $ZipPath) {
    Remove-Item $ZipPath -Force
}

# Создание ZIP без сжатия
$compression = [System.IO.Compression.CompressionLevel]::NoCompression
[System.IO.Compression.ZipFile]::CreateFromDirectory($BuildPath, $ZipPath, $compression, $false)

Write-Host "ZIP создан: $ZipPath"

# Удаляем временную сборочную директорию
Remove-Item $BuildPath -Recurse -Force
Write-Host "Временная директория удалена: $BuildPath"
