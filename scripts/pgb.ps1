
# pgb: run pgbackup-r2 through Docker, using .\.env and .\out. https://github.com/iotabyt/pgbackup-r2
#   pgb keygen | pgb list | pgb verify | pgb decrypt latest --out /out/backup.dump | pgb --help
# PG_MAJOR (from .env, default 16) picks the image. $env:PGB_DOCKER_ARGS adds docker options;
# $env:PGB_IMAGE overrides the image.
function pgb {
    $pg = '16'; $a = @()
    if (Test-Path .env) {
        $a += '--env-file', '.env'
        $m = Select-String -Path .env -Pattern '^PG_MAJOR=(\d+)' | Select-Object -Last 1
        if ($m) { $pg = $m.Matches[0].Groups[1].Value }
    }
    if ($env:PG_MAJOR) { $pg = $env:PG_MAJOR }
    if (-not [Console]::IsInputRedirected -and -not [Console]::IsOutputRedirected) { $a += '-it' }
    if ($args -contains 'decrypt') { New-Item -ItemType Directory -Force out | Out-Null }
    if (Test-Path out -PathType Container) { $a += '-v', "$((Resolve-Path out).Path):/out" }
    if ($IsLinux) { $a += '--user', "$(id -u):$(id -g)" }
    if ($env:PGB_DOCKER_ARGS) { $a += $env:PGB_DOCKER_ARGS -split '\s+' }
    $image = if ($env:PGB_IMAGE) { $env:PGB_IMAGE } else { "ghcr.io/iotabyt/pgbackup-r2:pg$pg" }
    docker run --rm @a $image @args
}
