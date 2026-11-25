# Installs pgb for PowerShell, from the latest release: saves pgb.ps1 in ~\.pgb and adds a
# pgb function to your $PROFILE, between marker comments that `pgb uninstall` looks for.
#   irm https://pgbackup-r2.iotabyt.com/install.ps1 | iex
# A specific release:
#   $env:PGB_VERSION = 'v0.4.3'; irm https://pgbackup-r2.iotabyt.com/install.ps1 | iex
# Running it again updates pgb.ps1 and leaves $PROFILE alone.

& {
    $ErrorActionPreference = 'Stop'
    $repo = 'iotabyt/pgbackup-r2'
    $api = "https://api.github.com/repos/$repo"

    # pgb comes from a release tag, never from main: main can be mid-change.
    $version = $env:PGB_VERSION
    if (-not $version) {
        # The latest published release, or else the highest vX.Y.Z tag.
        try { $version = (Invoke-RestMethod "$api/releases/latest").tag_name } catch { }
    }
    if (-not $version) {
        try {
            $version = (Invoke-RestMethod "$api/tags?per_page=100").name |
                Where-Object { $_ -match '^v\d+\.\d+\.\d+$' } |
                Sort-Object { [version]$_.Substring(1) } | Select-Object -Last 1
        } catch { }
    }
    if (-not $version) {
        throw ("pgb installer: couldn't find the latest pgb release: GitHub's API didn't answer. " +
            "Check your connection and try again (the API allows 60 requests an hour per address), " +
            "or name a release: `$env:PGB_VERSION = 'v0.4.3'; irm https://pgbackup-r2.iotabyt.com/install.ps1 | iex. " +
            "Releases: https://github.com/$repo/tags")
    }
    $url = if ($env:PGB_URL) { $env:PGB_URL } else { "https://raw.githubusercontent.com/$repo/$version/scripts/pgb.ps1" }

    $dir = Join-Path $HOME '.pgb'
    $dest = Join-Path $dir 'pgb.ps1'
    New-Item -ItemType Directory -Force $dir | Out-Null
    try { Invoke-RestMethod $url -OutFile "$dest.download" }
    catch { throw "pgb installer: couldn't download pgb $version from $url. Is $version a release? See https://github.com/$repo/tags" }
    Move-Item -Force "$dest.download" $dest
    "Installed pgb $version to $dest"

    # The function goes between markers, so `pgb uninstall` removes exactly this and nothing
    # else. An older install's single `function pgb` line is left as it is.
    $begin = '# >>> pgb: added by the pgbackup-r2 installer >>>'
    $end = '# <<< pgb <<<'
    New-Item -ItemType Directory -Force (Split-Path $PROFILE) | Out-Null
    $current = if (Test-Path $PROFILE) { [IO.File]::ReadAllText($PROFILE) } else { '' }
    if ($current.Contains($begin) -or $current -match "(?m)^function pgb \{ & '[^']*[\\/]\.pgb[\\/]pgb\.ps1' @args \}") {
        "Your `$PROFILE already has the pgb function."
    } else {
        $block = "$begin`nfunction pgb { & '$dest' @args }`n$end`n"
        if ($current -and -not $current.EndsWith("`n")) { $block = "`n$block" }
        Add-Content -Path $PROFILE -Value $block -NoNewline -Encoding UTF8
        "Added the pgb function to $PROFILE"
    }
    "Run: pgb help"
}

# Make pgb work in this window straight away; new windows get it from $PROFILE.
function global:pgb { & (Join-Path (Join-Path $HOME '.pgb') 'pgb.ps1') @args }
