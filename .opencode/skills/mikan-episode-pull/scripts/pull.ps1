#Requires -Version 5.1
<#
.SYNOPSIS
    把远端 NAS 上的已下载剧集复制到本机，并做 md5 校验。
.DESCRIPTION
    远端为 homelab（www.crimmy.top，登录 shell 是 nushell），
    qB 容器的 /downloads 挂载在宿主 /mnt/nas/qbittorrent。
.PARAMETER RemoteGlob
    相对 /mnt/nas/qbittorrent 的 glob，如 "anime/铁旅！邂逅铁道少女/*Tetsuryou*02*"。
.PARAMETER Destination
    本机目标目录，不存在会自动创建，如 "mikan-qb/tmp-watch/铁旅！邂逅铁道少女"。
.PARAMETER DryRun
    只列远端命中文件并计算 md5，不传输。
.EXAMPLE
    .\pull.ps1 -RemoteGlob "anime/xxx/*Tetsuryou*0[23]*" -Destination "C:\data\...\tmp-watch\xxx" -DryRun
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$RemoteGlob,
    [Parameter(Mandatory = $true)][string]$Destination,
    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'
$RemoteHost = 'homelab'
$RemoteRoot = '/mnt/nas/qbittorrent'

function Invoke-RemoteMd5 {
    param([string]$Glob)

    # nu 会展开 glob；不要用 bash -lc 包一层，引号会被吃掉
    $out = ssh $RemoteHost "md5sum $RemoteRoot/$Glob" 2>&1
    if ($LASTEXITCODE -ne 0) {
        throw "远端 md5sum 失败: $out"
    }
    $map = @{}
    foreach ($line in $out) {
        if ($line -match '^([0-9a-f]{32})\s+(.+)$') {
            $map[(Split-Path -Leaf $Matches[2])] = $Matches[1]
        }
    }
    return $map
}

Write-Host "远端清单: $RemoteRoot/$RemoteGlob"
$remoteMd5 = Invoke-RemoteMd5 -Glob $RemoteGlob
if ($remoteMd5.Count -eq 0) {
    throw "远端没有匹配文件，检查 RemoteGlob 是否正确（注意 nu 的 glob 与中文目录名）"
}
foreach ($name in $remoteMd5.Keys) {
    Write-Host ("  {0}  {1}" -f $remoteMd5[$name].Substring(0, 12), $name)
}

if ($DryRun) {
    Write-Host "`n[DryRun] 未传输。" -ForegroundColor Yellow
    return
}

if (-not (Test-Path -LiteralPath $Destination)) {
    New-Item -ItemType Directory -Force -Path $Destination | Out-Null
}

$start = Get-Date
scp ("{0}:{1}/{2}" -f $RemoteHost, $RemoteRoot, $RemoteGlob) $Destination
if ($LASTEXITCODE -ne 0) {
    throw "scp 失败（退出码 $LASTEXITCODE）"
}
$elapsed = [math]::Round(((Get-Date) - $start).TotalSeconds, 1)

$rows = foreach ($name in $remoteMd5.Keys) {
    $local = Join-Path $Destination $name
    if (-not (Test-Path -LiteralPath $local)) {
        [pscustomobject]@{ 文件 = $name; 大小MB = $null; MD5 = 'MISSING' }
        continue
    }
    $hash = (Get-FileHash -LiteralPath $local -Algorithm MD5).Hash.ToLower()
    [pscustomobject]@{
        文件   = $name
        大小MB = [math]::Round((Get-Item -LiteralPath $local).Length / 1MB, 1)
        MD5    = if ($hash -eq $remoteMd5[$name]) { 'OK' } else { "MISMATCH ($hash)" }
    }
}

$rows | Format-Table -AutoSize
Write-Host ("耗时 {0}s，目标目录 {1}" -f $elapsed, $Destination)

$bad = @($rows | Where-Object { $_.MD5 -ne 'OK' })
if ($bad.Count -gt 0) {
    Write-Warning "有 $($bad.Count) 个文件未通过校验，删除后重传（scp 不支持断点续传）"
    exit 1
}
