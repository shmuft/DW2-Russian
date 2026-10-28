param(
    [Parameter(Mandatory=$true)]
    [ValidatePattern('^\d+\.\d+\.\d+\.\d+$')]
    [string]$Version,

    [switch]$Beta
)

Add-Type -AssemblyName System.IO.Compression.FileSystem

$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$BuildRoot = Join-Path $ProjectRoot "build"
$ReleasesPath = Join-Path $ProjectRoot "releases"

if ($Beta) {
    $ModLoaderName = "DW2Russian_Beta_${Version}_ModLoader"
    $LegacyName = "DW2Russian_Beta_${Version}_full"
    $SteamName = "DW2Russian_Beta_${Version}_Steam"
} else {
    $ModLoaderName = "DW2Russian_${Version}_ModLoader"
    $LegacyName = "DW2Russian_${Version}_full"
    $SteamName = "DW2Russian_${Version}_Steam"
}

$ModLoaderBuildPath = Join-Path $BuildRoot "_modloader_tmp_${Version}"
$SteamBuildPath = Join-Path $BuildRoot "_steam_tmp_${Version}"
$NewModPath = $ModLoaderBuildPath
$LegacyBuildPath = Join-Path $BuildRoot "_legacy_tmp_${Version}"
$LegacyZipPath = Join-Path $ReleasesPath ($LegacyName + ".zip")
$ModLoaderReleaseZipPath = Join-Path $ReleasesPath ($ModLoaderName + ".zip")
$SteamReleaseZipPath = Join-Path $ReleasesPath ($SteamName + ".zip")

New-Item -ItemType Directory -Path $BuildRoot -Force | Out-Null
New-Item -ItemType Directory -Path $ReleasesPath -Force | Out-Null

foreach ($buildPath in @($ModLoaderBuildPath, $SteamBuildPath, $LegacyBuildPath)) {
    if (Test-Path $buildPath) {
        Remove-Item $buildPath -Recurse -Force
    }
}

foreach ($releasePath in @($LegacyZipPath, $ModLoaderReleaseZipPath, $SteamReleaseZipPath)) {
    if (Test-Path $releasePath) {
        Remove-Item $releasePath -Force
    }
}

New-Item -ItemType Directory -Path $ModLoaderBuildPath -Force | Out-Null
New-Item -ItemType Directory -Path $SteamBuildPath -Force | Out-Null
New-Item -ItemType Directory -Path $LegacyBuildPath -Force | Out-Null

try {
    $PythonExecutable = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
    if (-not (Test-Path $PythonExecutable)) {
        $PythonExecutable = (Get-Command python -ErrorAction Stop).Source
    }

    Push-Location $ProjectRoot
    try {
        & $PythonExecutable "batch_translate.py" `
            "--target-version" $Version `
            "--export-modloader" $NewModPath
        if ($LASTEXITCODE -ne 0) {
            throw "Экспорт перевода для DW2 Mod Launcher завершился с ошибкой."
        }
    }
    finally {
        Pop-Location
    }

    $ThumbSource = Join-Path $ProjectRoot "steam-thumb.jpg"
    if (-not (Test-Path $ThumbSource)) {
        throw "Не найдена обложка мода: $ThumbSource"
    }
    Copy-Item -Path $ThumbSource -Destination $NewModPath -Force

    $SourceDirs = @(
        "$Version\Russian\DLC Atuuk and Wekkarus\",
        "$Version\Russian\DLC Ikkuro and Dhayut\",
        "$Version\Russian\DLC Quameno and Gizureans\",
        "$Version\Russian\DLC Return of the Shakturi\",
        "$Version\Russian\DLC Shadows Rising\",
        "$Version\Russian\DW2\",
        "data"
    )

    foreach ($relativePath in $SourceDirs) {
        $sourcePath = Join-Path $ProjectRoot $relativePath
        if (-not (Test-Path $sourcePath)) {
            Write-Host "Не найдено: $relativePath"
            continue
        }

        Copy-Item -Path (Join-Path $sourcePath "*") -Destination $LegacyBuildPath -Recurse -Force
    }

    $ModJsonSource = Join-Path $ProjectRoot "mod.json"
    if (-not (Test-Path $ModJsonSource)) {
        throw "Не найден манифест старого мода: $ModJsonSource"
    }

    $modJson = Get-Content $ModJsonSource -Raw | ConvertFrom-Json
    $modJson.version = $Version
    $modJson | ConvertTo-Json -Depth 10 |
        Set-Content (Join-Path $LegacyBuildPath "mod.json") -Encoding UTF8

    $ThumbSource = Join-Path $ProjectRoot "steam-thumb.jpg"
    if (Test-Path $ThumbSource) {
        Copy-Item -Path $ThumbSource -Destination $LegacyBuildPath -Force
    }

    $modLoaderReadme = @"
Файлы мода для DW2 Mod Launcher находятся в корне архива. Распакуйте архив и добавьте
эту директорию в список модов загрузчика или скопируйте её в директорию модов.
"@
    [System.IO.File]::WriteAllText(
        (Join-Path $ModLoaderBuildPath "README.txt"),
        $modLoaderReadme + [Environment]::NewLine,
        [System.Text.UTF8Encoding]::new($false)
    )

    $compression = [System.IO.Compression.CompressionLevel]::NoCompression
    [System.IO.Compression.ZipFile]::CreateFromDirectory(
        $LegacyBuildPath,
        $LegacyZipPath,
        $compression,
        $false
    )

    $steamOldModPath = Join-Path $SteamBuildPath "old_mod"
    New-Item -ItemType Directory -Path $steamOldModPath -Force | Out-Null
    Copy-Item -Path (Join-Path $ModLoaderBuildPath "*") -Destination $SteamBuildPath -Recurse -Force
    Copy-Item -Path $LegacyZipPath -Destination $steamOldModPath -Force

    $steamReadme = @"
Перевод Distant Worlds 2 перешёл на новую модель модов для DW2 Mod Launcher.

Файлы мода для загрузчика находятся в корне архива. Распакуйте архив и добавьте
эту директорию в список модов загрузчика или скопируйте её в директорию модов.

Если необходимо использовать старый вариант мода для ручной установки,
архив находится в директории old_mod.
"@
    [System.IO.File]::WriteAllText(
        (Join-Path $SteamBuildPath "README.txt"),
        $steamReadme + [Environment]::NewLine,
        [System.Text.UTF8Encoding]::new($false)
    )

    [System.IO.Compression.ZipFile]::CreateFromDirectory(
        $ModLoaderBuildPath,
        $ModLoaderReleaseZipPath,
        $compression,
        $false
    )

    [System.IO.Compression.ZipFile]::CreateFromDirectory(
        $SteamBuildPath,
        $SteamReleaseZipPath,
        $compression,
        $false
    )
}
finally {
    foreach ($buildPath in @($ModLoaderBuildPath, $SteamBuildPath, $LegacyBuildPath)) {
        if (Test-Path $buildPath) {
            Remove-Item $buildPath -Recurse -Force
        }
    }
}

Write-Host "Временные файлы сборки удалены из: $BuildRoot"
Write-Host "Архивы созданы:"
Write-Host "  $ModLoaderReleaseZipPath"
Write-Host "  $LegacyZipPath"
Write-Host "  $SteamReleaseZipPath"
