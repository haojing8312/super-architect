param(
    [string]$BuildRoot = 'E:/codex-work/super-architect/local-run',
    [string]$DataDir = 'E:/codex-work/super-architect/client-data',
    [string]$PythonExecutable = '',
    [int]$Port = 33829,
    [switch]$PrepareOnly
)
$ErrorActionPreference = 'Stop'
$taskRepoRoot = $PSScriptRoot
$taskBuildRoot = [IO.Path]::GetFullPath($BuildRoot)
if (-not $taskBuildRoot.StartsWith('E:\', [StringComparison]::OrdinalIgnoreCase)) { throw '构建及缓存目录必须位于 E 盘。' }
foreach ($taskDirectory in @($taskBuildRoot, "$taskBuildRoot/temp", "$taskBuildRoot/cache/pip", "$taskBuildRoot/cache/npm")) {
    New-Item -ItemType Directory -Force -Path $taskDirectory | Out-Null
}
$env:TEMP = "$taskBuildRoot/temp"
$env:TMP = $env:TEMP
$env:TMPDIR = $env:TEMP
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:PYTHONPYCACHEPREFIX = "$taskBuildRoot/cache/pycache"
$env:PIP_CACHE_DIR = "$taskBuildRoot/cache/pip"
$env:npm_config_cache = "$taskBuildRoot/cache/npm"
$taskSourceRoot = $taskRepoRoot
if (-not $taskSourceRoot.StartsWith('E:\', [StringComparison]::OrdinalIgnoreCase)) {
    $taskRevision = (& git -C $taskRepoRoot rev-parse HEAD).Trim()
    if ($LASTEXITCODE -ne 0) { throw '请从 Git 仓库运行启动脚本。' }
    $taskSourceRoot = Join-Path $taskBuildRoot "source-$taskRevision"
    if (-not (Test-Path -LiteralPath $taskSourceRoot)) {
        $taskArchive = Join-Path $taskBuildRoot "source-$taskRevision.zip"
        & git -C $taskRepoRoot archive --format=zip "--output=$taskArchive" HEAD
        if ($LASTEXITCODE -ne 0) { throw '无法导出已提交源码。' }
        Expand-Archive -LiteralPath $taskArchive -DestinationPath $taskSourceRoot
    }
}
if (-not $PythonExecutable) {
    $taskExistingPython = Join-Path $taskRepoRoot '.venv/Scripts/python.exe'
    $PythonExecutable = if (Test-Path -LiteralPath $taskExistingPython) { $taskExistingPython } else { 'python' }
}
& $PythonExecutable -c 'import sys; assert sys.version_info >= (3,11), "需要 Python 3.11 或以上版本；可用 -PythonExecutable 指定"'
if ($LASTEXITCODE -ne 0) { throw 'Python 版本检查失败。' }
$taskPython = Join-Path $taskBuildRoot 'venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $taskPython)) {
    & $PythonExecutable -m venv (Join-Path $taskBuildRoot 'venv')
    if ($LASTEXITCODE -ne 0) { throw '创建 E 盘虚拟环境失败。' }
}
$taskRequirements = (Get-FileHash -LiteralPath (Join-Path $taskSourceRoot 'pyproject.toml') -Algorithm SHA256).Hash
$taskMarker = Join-Path $taskBuildRoot 'dependencies.sha256'
if (-not (Test-Path -LiteralPath $taskMarker) -or (Get-Content -LiteralPath $taskMarker -Raw).Trim() -ne $taskRequirements) {
    & $taskPython -m pip install -e "${taskSourceRoot}[desktop]"
    if ($LASTEXITCODE -ne 0) { throw '安装公开 Python 依赖失败。' }
    Set-Content -LiteralPath $taskMarker -Value $taskRequirements
}
if (-not (Test-Path -LiteralPath (Join-Path $taskSourceRoot 'frontend/dist/index.html'))) {
    & npm.cmd ci --prefix (Join-Path $taskSourceRoot 'frontend')
    if ($LASTEXITCODE -ne 0) { throw '安装前端依赖失败。' }
    & npm.cmd run build --prefix (Join-Path $taskSourceRoot 'frontend')
    if ($LASTEXITCODE -ne 0) { throw '前端与室内编辑器构建失败。' }
}
$env:PYTHONPATH = Join-Path $taskSourceRoot 'backend'
if ($PrepareOnly) { Write-Output "本地环境已准备：$taskSourceRoot"; return }
& $taskPython -m architecture_workbench desktop --data-dir $DataDir --port $Port
if ($LASTEXITCODE -ne 0) { throw '工作台启动失败，请查看上方错误。' }
