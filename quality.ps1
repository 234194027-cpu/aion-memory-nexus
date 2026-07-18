$ErrorActionPreference = 'Stop'

function Invoke-Checked {
    param(
        [Parameter(Mandatory = $true)]
        [scriptblock]$Command,
        [Parameter(Mandatory = $true)]
        [string]$Name
    )

    & $Command
    if ($LASTEXITCODE -ne 0) {
        throw "$Name failed with exit code $LASTEXITCODE"
    }
}

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Push-Location $root
try {
    Invoke-Checked { python -X utf8 -m ruff check src tests } 'Ruff'
    Invoke-Checked { python -X utf8 -m mypy } 'mypy'

    $boundaryViolations = Get-ChildItem -LiteralPath (Join-Path $root 'src\memory\api') -Filter '*.py' |
        Select-String -Pattern '^\s*from\s+src\.shared\.llm\.providers\s+import'
    if ($boundaryViolations) {
        $boundaryViolations | ForEach-Object { Write-Error $_.ToString() }
        throw 'Import boundary failed: memory API modules must call MemoryAnswerService.'
    }

    Invoke-Checked { python -X utf8 -m pytest tests -q -p no:faulthandler } 'pytest'
    Invoke-Checked { python -X utf8 scripts/check_migrations.py } 'migration check'

    Push-Location (Join-Path $root 'admin-web')
    try {
        Invoke-Checked { npm run lint } 'frontend lint'
        Invoke-Checked { npm run typecheck } 'frontend typecheck'
        Invoke-Checked { npm run build } 'frontend build'
    }
    finally {
        Pop-Location
    }
}
finally {
    Pop-Location
}
