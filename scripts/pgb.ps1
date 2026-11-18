# pgb: pgbackup-r2 through Docker, using the .env and out\ in the current folder.
# https://github.com/iotabyt/pgbackup-r2 (scripts/pgb is the bash twin.)
#
# Install: save this file as ~\.pgb\pgb.ps1 and add to $PROFILE:
#   function pgb { & "$HOME\.pgb\pgb.ps1" @args }
# PG_MAJOR (from .env, default 16) picks the image, and PGB_NETWORK joins a Docker network.
# For developers: $env:PGB_IMAGE runs a different image, such as a local build (CONTRIBUTING.md).

$PgbVersion = '0.2.0'
$Repo = 'ghcr.io/iotabyt/pgbackup-r2'
$ErrorActionPreference = 'Stop'

function Fail([string]$msg) { [Console]::Error.WriteLine("pgb: $msg"); exit 1 }

# The last KEY=value for KEY in .\.env.
function EnvValue([string]$key) {
    if (-not (Test-Path .env -PathType Leaf)) { return '' }
    $m = Select-String -Path .env -Pattern "^$key=(.*)$" | Select-Object -Last 1
    if ($m) { return $m.Matches[0].Groups[1].Value.TrimEnd("`r") }
    return ''
}

$pg = $env:PG_MAJOR
if (-not $pg) { $pg = EnvValue 'PG_MAJOR' }
if (-not $pg) { $pg = '16' }
if ($pg -notmatch '^\d+$') { Fail "PG_MAJOR must be a number like 16, not '$pg'. Fix it in .env." }
$image = if ($env:PGB_IMAGE) { $env:PGB_IMAGE } else { "${Repo}:pg$pg" }

# decrypt writes to /out: create .\out first, or Docker would create it owned by root.
if ($args -contains 'decrypt') { New-Item -ItemType Directory -Force out | Out-Null }

# Options every container gets: .env, .\out and, on Linux, your user.
$common = @()
if (Test-Path .env -PathType Leaf) { $common += '--env-file', '.env' }
# Tells the image it runs under pgb, so its help and messages say `pgb`.
$common += '-e', "PGB_VERSION=$PgbVersion"
if (Test-Path out -PathType Container) { $common += '-v', "$((Resolve-Path out).Path):/out" }
if ($IsLinux) { $common += '--user', "$(id -u):$(id -g)" }
$tty = @()
if (-not [Console]::IsInputRedirected -and -not [Console]::IsOutputRedirected) { $tty = @('-it') }

# Native commands: don't let a non-zero exit or stderr output stop the script.
function Quiet { param([scriptblock]$block) $ErrorActionPreference = 'Continue'; & $block 2>$null }

function Note([string]$msg) { [Console]::Error.WriteLine("pgb: $msg") }

# How containers that talk to the database reach it. PGB_NETWORK joins an existing Docker
# network (e.g. a Compose project's myapp_default). Otherwise a database on localhost is
# reached with --network host on Linux, or through host.docker.internal on Docker Desktop.
$LocalUrlRe = '^(postgres(ql)?://([^@/]*@)?)(localhost|127\.0\.0\.1)([:/?].*)?$'
function Add-DbNetwork {
    $network = $env:PGB_NETWORK
    if (-not $network) { $network = EnvValue 'PGB_NETWORK' }
    if ($network) { $script:common += '--network', $network; return }
    $desktop = $null
    foreach ($var in 'DATABASE_URL', 'RESTORE_DATABASE_URL') {
        $url = EnvValue $var
        if ($url -cnotmatch $LocalUrlRe) { continue }
        $rewritten = $Matches[1] + 'host.docker.internal' + $Matches[5]
        if ($null -eq $desktop) {
            $desktop = $true
            if ($IsLinux) {
                $os = Quiet { docker info -f '{{.OperatingSystem}}' }
                if ("$os" -notmatch 'Docker Desktop') { $desktop = $false }
            }
        }
        if (-not $desktop) {
            Note "$var is on localhost: using --network host"
            $script:common += '--network', 'host'
            return
        }
        # Through the environment rather than -e VAR=value, so the password stays out of ps.
        Set-Item "env:$var" -Value $rewritten
        $script:common += '-e', $var
        Note "$var is on localhost: using host.docker.internal (Docker Desktop)"
    }
}

function ImageVersion { # e.g. "0.2.0", or '' if the image isn't here
    Quiet { docker image inspect $image } | Out-Null
    if ($LASTEXITCODE -ne 0) { return '' }
    $v = Quiet { docker run --rm $image --version }
    if ($v) { return ($v -split ' ')[-1] }
    return ''
}

function Show-Help([string[]]$rest) {
    if ($rest.Count -gt 0) {
        $which = $rest[0]
        if ($OwnHelp.ContainsKey($which)) { $OwnHelp[$which]; return }
        docker run --rm @tty -e "PGB_VERSION=$PgbVersion" $image $which --help
        exit $LASTEXITCODE
    }
    @"
pgb ${PgbVersion}: pgbackup-r2 through Docker, using ./.env (image $image)

  pgb init                    write a commented .env here, and create out/
  pgb init --detect           suggest TIERS from the bucket's folders (asks before writing)
  pgb doctor                  check Docker, .env, the database, the bucket and the keys
  pgb keygen                  print a new key pair
  pgb backup                  back up DATABASE_URL now
  pgb list                    list backups, and when the last drill ran
  pgb verify [STAMP]          download, check and decrypt a backup; no database needed
  pgb drill [STAMP]           restore into a throwaway Postgres and check every table
  pgb decrypt latest --out /out/backup.dump
  pgb restore [STAMP]         restore into RESTORE_DATABASE_URL
  pgb prune [--dry-run]       delete backups past RETENTION_DAYS
  pgb nightly start|stop|status|logs
                              back up every day at BACKUP_AT, in a background container
  pgb update                  pull the latest image for PG_MAJOR
  pgb version                 pgb's and the image's versions

pgb help <command> shows what a command does, with examples.
Docs: https://pgbackup-r2.iotabyt.com
"@
}

# Help for pgb's own commands. The image's commands have their own (pgb help backup).
$OwnHelp = @{
    'init' = @'
Write a commented .env here, and create out/.

Usage: pgb init [--detect] [--yes]

Never overwrites an existing .env. With --detect and an existing .env, it looks at the
bucket's folders and lifecycle rules and suggests TIERS. It asks before writing; --yes
writes without asking.

Examples:
  pgb init                          # set up a folder for one database
  pgb init --detect                 # fit into the folders your bucket already has
'@
    'drill' = @'
Restore a backup into a throwaway database and check every table.

Usage: pgb drill [STAMP]

Starts postgres:<PG_MAJOR> on a private Docker network, restores the backup, compares
every table's row count with the manifest, and removes the database afterwards.

Examples:
  pgb drill                         # the latest backup
  pgb drill 2026-11-18T020000Z      # a specific one
'@
    'nightly' = @'
Back up every day at BACKUP_AT, in a background container.

Usage: pgb nightly start|stop|status|logs [-f]

The container is named pgbackup-<S3_PREFIX> and restarts with Docker.

Examples:
  pgb nightly start                 # start it; does nothing if it's already running
  pgb nightly status                # running or not, and the last log lines
  pgb nightly logs -f               # follow the log
'@
    'update' = @'
Pull the latest image for PG_MAJOR, and show the old and new versions.

Usage: pgb update

Examples:
  pgb update
'@
    'version' = @'
Show pgb's version and the version in the image.

Usage: pgb version

Examples:
  pgb version
'@
    'help' = @'
Show the commands, or what one command does, with examples.

Usage: pgb help [COMMAND]

Examples:
  pgb help
  pgb help drill
'@
}

# pgb init --detect [--yes]: suggest TIERS/PRUNE from the bucket and, if you agree, put them
# in .env (replacing any TIERS= and PRUNE= lines already there).
function Invoke-Detect([bool]$yes) {
    # No -t: the explanation comes on stderr, the .env lines on stdout.
    $lines = @(docker run --rm @common $image detect --env-lines)
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    $lines = @($lines | Where-Object { $_ })
    if ($lines.Count -eq 0) { return }
    ''
    $lines | ForEach-Object { "    $_" }
    ''
    if (-not $yes) {
        if ([Console]::IsInputRedirected) {
            'Not a terminal, so .env is unchanged. Add the lines yourself, or rerun with --yes.'
            return
        }
        $answer = Read-Host 'Write these into .env? [y/N]'
        if ($answer -notmatch '^[yY]') { '.env unchanged.'; return }
    }
    $kept = @(Get-Content .env | Where-Object { $_ -notmatch '^(TIERS|PRUNE)=' })
    $text = (($kept + $lines) -join "`n") + "`n"
    # Same file, rewritten in place: it keeps its permissions. LF and no BOM, for docker.
    [IO.File]::WriteAllText((Join-Path (Get-Location).Path '.env'), $text, (New-Object Text.UTF8Encoding($false)))
    'Updated .env. pgb doctor shows how each tier looks now.'
}

function Invoke-Init([string[]]$rest) {
    $detect = $false; $yes = $false
    foreach ($a in $rest) {
        switch ($a) {
            '--detect' { $detect = $true }
            { $_ -in '--yes', '-y' } { $yes = $true }
            default { Fail "pgb init: unknown option $a (try --detect)" }
        }
    }
    if ($detect -and (Test-Path .env -PathType Leaf)) { Invoke-Detect $yes; return }
    if (Test-Path .env) { Fail '.env already exists here; not touching it (pgb init --detect looks at the bucket)' }
    $template = @'
# pgbackup-r2 settings for this folder, read by `pgb`. Keep this file out of git: it holds
# passwords. Write values without quotes, and keep comments on their own lines.
# Every setting is explained at https://pgbackup-r2.iotabyt.com/reference/environment/

# Your Postgres server's major version (run SHOW server_version;)
PG_MAJOR=16
# The database to back up, as a direct connection: postgresql://user:password@host:5432/db
DATABASE_URL=
# The bucket's S3 endpoint. R2: https://<account-id>.r2.cloudflarestorage.com
S3_ENDPOINT=
# The bucket's name
S3_BUCKET=
# An API token for the bucket, with read and write access
AWS_ACCESS_KEY_ID=
AWS_SECRET_ACCESS_KEY=
# The PUBLIC key from `pgb keygen` (age1...). Backups are encrypted to it.
AGE_RECIPIENTS=

# Optional. Uncomment a line to change it; the value shown is the default.

# The PRIVATE key from `pgb keygen`, for verify, drill, decrypt and restore. Laptop only.
# AGE_IDENTITY=
# A folder in the bucket for this app. `pgb nightly` names its container after it.
# S3_PREFIX=
# The bucket's region: auto suits R2. For AWS S3 or Backblaze B2, the real region.
# S3_REGION=auto
# A database in Docker Compose here: join its network (`docker network ls` lists them)
# PGB_NETWORK=
# UTC times for `pgb nightly`, comma-separated
# BACKUP_AT=02:00
# Delete backups older than this many days...
# RETENTION_DAYS=30
# ...but always keep the newest few
# MIN_KEEP=7
# Delete old backups after each backup (default false when TIERS is set)
# PRUNE=true
# Use folders you already have, e.g. "daily=daily/ monthly=monthly/" (pgb init --detect)
# TIERS=
# A URL pinged after each successful backup or drill, and one for failures
# HEARTBEAT_URL=
# HEARTBEAT_FAIL_URL=
# Where `pgb restore` restores to
# RESTORE_DATABASE_URL=
'@
    # LF endings and no BOM: docker --env-file would keep a CR or BOM as part of the text.
    $text = ($template -replace "`r`n", "`n") + "`n"
    $path = Join-Path (Get-Location).Path '.env'
    try {
        # CreateNew fails rather than overwrite a file that appeared meanwhile.
        $stream = [IO.File]::Open($path, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write)
        $bytes = (New-Object Text.UTF8Encoding($false)).GetBytes($text)
        $stream.Write($bytes, 0, $bytes.Length)
        $stream.Close()
    } catch { Fail "couldn't write .env: $($_.Exception.Message). Check you can write to this folder." }
    if ($IsLinux -or $IsMacOS) { chmod 600 .env }
    New-Item -ItemType Directory -Force out | Out-Null
    @'
Wrote .env (readable only by you) and created out/.

Next:
  1. pgb keygen      make a key pair; save the PRIVATE key in your password manager
  2. Fill in .env    the 7 lines at the top; everything under "Optional" can wait
  3. pgb doctor      check everything is set up right
  4. pgb backup      take a first backup
  5. pgb verify      check it downloads and decrypts
  6. pgb drill       restore it into a throwaway Postgres and compare row counts
Then `pgb nightly start` backs up every night at BACKUP_AT (02:00 UTC).
'@
    if ($detect) {
        ''
        'To look for existing folders: fill in the S3_* settings, then run pgb init --detect again.'
    }
}

function New-Hex([int]$n) {
    $b = New-Object byte[] $n
    [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($b)
    return -join ($b | ForEach-Object { $_.ToString('x2') })
}

function Invoke-Drill([string[]]$rest) {
    $id = New-Hex 4; $pw = New-Hex 16
    $net = "pgb-drill-$id"; $db = "pgb-drill-$id-db"; $run = "pgb-drill-$id-run"
    # Postgres 18 images keep their data under /var/lib/postgresql, older ones in .../data.
    $vol = if ([int]$pg -ge 18) { '/var/lib/postgresql' } else { '/var/lib/postgresql/data' }
    $saved = @{ POSTGRES_PASSWORD = $env:POSTGRES_PASSWORD; DRILL_DATABASE_URL = $env:DRILL_DATABASE_URL }
    $rc = 1
    try {
        [Console]::Error.WriteLine("pgb: starting a throwaway postgres:$pg ($db)")
        docker network create $net | Out-Null
        if ($LASTEXITCODE -ne 0) { Fail "couldn't create the Docker network $net" }
        # The password goes in through the environment, not the command line, to stay out of ps.
        $env:POSTGRES_PASSWORD = $pw
        docker run -d --name $db --network $net --tmpfs $vol `
            -e POSTGRES_PASSWORD -e POSTGRES_DB=drill "postgres:$pg" | Out-Null
        if ($LASTEXITCODE -ne 0) { Fail "couldn't start postgres:$pg" }

        # -h 127.0.0.1: the image's first-boot server only listens on a socket, so this
        # waits for the real one.
        $waited = 0
        while ($true) {
            Quiet { docker exec $db pg_isready -q -h 127.0.0.1 -U postgres -d drill } | Out-Null
            if ($LASTEXITCODE -eq 0) { break }
            if ($waited -ge 60) {
                Quiet { docker logs --tail 20 $db } | Out-Host
                Fail "the throwaway Postgres wasn't ready after 60s. Check Docker has memory to spare, then run pgb drill again."
            }
            Start-Sleep -Seconds 1
            $waited++
        }

        $env:DRILL_DATABASE_URL = "postgresql://postgres:$pw@${db}:5432/drill"
        docker run --rm --name $run @tty @common --network $net -e DRILL_DATABASE_URL $image drill @rest
        $rc = $LASTEXITCODE
    } finally {
        foreach ($k in $saved.Keys) { Set-Item "env:$k" -Value $saved[$k] -ErrorAction SilentlyContinue }
        if ($null -eq $saved.POSTGRES_PASSWORD) { Remove-Item env:POSTGRES_PASSWORD -ErrorAction SilentlyContinue }
        if ($null -eq $saved.DRILL_DATABASE_URL) { Remove-Item env:DRILL_DATABASE_URL -ErrorAction SilentlyContinue }
        Quiet { docker rm -fv $run $db } | Out-Null
        Quiet { docker network rm $net } | Out-Null
    }
    exit $rc
}

function Invoke-Doctor {
    Quiet { docker info } | Out-Null
    if ($LASTEXITCODE -eq 0) { "$([char]0x2713) Docker is running" }
    else {
        "$([char]0x2717) Docker isn't running, or you can't reach it"
        '    Start Docker Desktop, or on Linux: sudo systemctl start docker (and add yourself'
        '    to the docker group: sudo usermod -aG docker $USER, then log in again)'
        exit 1
    }
    if (Test-Path .env -PathType Leaf) { "$([char]0x2713) .env found in $((Get-Location).Path)" }
    else {
        "$([char]0x2717) No .env in $((Get-Location).Path)"
        '    Run pgb init here, or cd to the folder that has your .env'
        exit 1
    }
    Add-DbNetwork
    # The names in .env (never the values), so doctor can catch typos like S3_BUKET.
    $env:PGB_ENV_KEYS = (Select-String -Path .env -Pattern '^([A-Za-z_][A-Za-z0-9_]*)=' |
        ForEach-Object { $_.Matches[0].Groups[1].Value }) -join ','
    docker run --rm @tty @common -e PGB_ENV_KEYS $image doctor
    exit $LASTEXITCODE
}

function NightlyName {
    $prefix = (EnvValue 'S3_PREFIX') -replace '[^A-Za-z0-9_.-]', '-'
    $prefix = $prefix.TrimStart('-', '.').TrimEnd('-')
    if ($prefix) { return "pgbackup-$prefix" } else { return 'pgbackup' }
}

function Invoke-Nightly([string[]]$rest) {
    $name = NightlyName
    $state = Quiet { docker inspect -f '{{.State.Status}}' $name }
    if ($LASTEXITCODE -ne 0) { $state = '' }
    $sub = if ($rest.Count -gt 0) { $rest[0] } else { '' }
    switch ($sub) {
        'start' {
            if (-not (Test-Path .env -PathType Leaf)) { Fail 'no .env here; run pgb init first' }
            if ($state -eq 'running') {
                "$name is already running. pgb nightly status shows how it's doing."
                exit 0
            }
            if ($state) { Quiet { docker rm -f $name } | Out-Null }
            if (EnvValue 'AGE_IDENTITY') {
                [Console]::Error.WriteLine("pgb: note: .env has AGE_IDENTITY; backups don't need it. Leave it out on a server.")
            }
            Add-DbNetwork
            docker run -d --name $name --restart unless-stopped @common $image schedule | Out-Null
            if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
            $at = EnvValue 'BACKUP_AT'; if (-not $at) { $at = '02:00' }
            "Started $name ($image). It backs up at $at UTC."
            'pgb nightly status | pgb nightly logs -f | pgb nightly stop'
            exit 0
        }
        'stop' {
            if (-not $state) { "$name isn't running."; exit 0 }
            Quiet { docker rm -f $name } | Out-Null
            "Stopped and removed $name. Backups already taken stay in the bucket."
            exit 0
        }
        'status' {
            if (-not $state) { "${name}: not running. Start it with pgb nightly start."; exit 3 }
            (docker inspect -f '{{.Name}}: {{.State.Status}} since {{.State.StartedAt}}, image {{.Config.Image}}, restarts {{.RestartCount}}' $name) -replace '^/', ''
            '--- last log lines'
            $ErrorActionPreference = 'Continue'
            docker logs --tail 10 $name 2>&1 | ForEach-Object { "$_" }
            if ($state -eq 'running') { exit 0 } else { exit 1 }
        }
        'logs' {
            if (-not $state) { Fail "$name isn't running. Start it with pgb nightly start." }
            $more = @($rest | Select-Object -Skip 1)
            $ErrorActionPreference = 'Continue'
            docker logs --tail 100 @more $name 2>&1 | ForEach-Object { "$_" }
            exit $LASTEXITCODE
        }
        default { Fail 'usage: pgb nightly start|stop|status|logs [-f]' }
    }
}

function Invoke-Update {
    $old = ImageVersion
    docker pull $image
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    $new = ImageVersion
    if (-not $old) { "Pulled ${image}: pgbackup-r2 $new" }
    elseif ($old -eq $new) { "$image is up to date: pgbackup-r2 $new" }
    else { "Updated ${image}: pgbackup-r2 $old -> $new" }
    $name = NightlyName
    $running = Quiet { docker inspect -f '{{.Image}}' $name }
    if ($LASTEXITCODE -eq 0 -and $running -and $running -ne (docker image inspect -f '{{.Id}}' $image)) {
        "$name still runs the old image. To switch: pgb nightly stop && pgb nightly start"
    }
}

function Show-Version {
    $v = ImageVersion
    "pgb $PgbVersion"
    if ($v) { "image ${image}: pgbackup-r2 $v" } else { "image ${image}: not pulled yet (pgb update)" }
}

$cmd = if ($args.Count -gt 0) { [string]$args[0] } else { 'help' }
$rest = @($args | Select-Object -Skip 1)
# pgb runs inside your PowerShell session, so put back any variables it changed for Docker.
$savedEnv = @{
    DATABASE_URL = $env:DATABASE_URL; RESTORE_DATABASE_URL = $env:RESTORE_DATABASE_URL
    PGB_ENV_KEYS = $env:PGB_ENV_KEYS
}
# --help on pgb's own commands shows help, rather than doing the thing.
if (($rest -contains '-h' -or $rest -contains '--help') -and
    $cmd -in 'init', 'drill', 'nightly', 'update', 'version') {
    $OwnHelp[$cmd]
    exit 0
}
try {
    switch -CaseSensitive ($cmd) {
        { $_ -in 'help', '-h', '--help' } { Show-Help $rest; exit 0 }
        'init' { Invoke-Init $rest; exit 0 }
        'doctor' { Invoke-Doctor }
        'drill' { Invoke-Drill $rest }
        'nightly' { Invoke-Nightly $rest }
        'update' { Invoke-Update; exit 0 }
        { $_ -in 'version', '--version' } { Show-Version; exit 0 }
        default {
            if ($cmd -ne 'keygen') { Add-DbNetwork }
            docker run --rm @tty @common $image @args
            exit $LASTEXITCODE
        }
    }
} finally {
    foreach ($k in $savedEnv.Keys) {
        if ($null -eq $savedEnv[$k]) { Remove-Item "env:$k" -ErrorAction Ignore }
        else { Set-Item "env:$k" -Value $savedEnv[$k] }
    }
}
