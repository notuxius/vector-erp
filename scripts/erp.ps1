param(
    [ValidateSet('start', 'stop', 'status', 'logs', 'backup', 'demo', 'demo-production', 'verify')]
    [string]$Action = 'status'
)
$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Push-Location -LiteralPath $ProjectRoot
try {
    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
        throw 'Docker Desktop is required.'
    }
    if ($Action -eq 'start') {
        python scripts/init_env.py
        if ($LASTEXITCODE -ne 0) { throw 'Could not initialize local credentials.' }
    }
    if (-not (Test-Path -LiteralPath '.env')) {
        throw 'Run .\scripts\erp.ps1 start first.'
    }
    docker info --format '{{.OSType}}' 2>$null
    if ($LASTEXITCODE -ne 0) { throw 'Start Docker Desktop with Linux containers, then retry.' }
    $ComposeArgs = @('compose', '--project-name', 'vector-erp', '--env-file', '.env', '-f', 'compose.yaml')
    switch ($Action) {
        'start' {
            & docker @ComposeArgs build backend
            if ($LASTEXITCODE -ne 0) { throw 'Building the Vector ERP image failed.' }
            & docker @ComposeArgs up -d --quiet-pull
            if ($LASTEXITCODE -ne 0) { throw 'Vector ERP startup failed.' }
            Write-Host 'Containers started. Site installation may take several minutes.'
            Write-Host 'Run .\scripts\erp.ps1 logs to inspect installation progress.'
            Write-Host 'Open http://localhost:8080 (or HTTP_PORT in .env). User: Administrator; password: ADMIN_PASSWORD in .env.'
        }
        'stop' { & docker @ComposeArgs stop }
        'status' { & docker @ComposeArgs ps -a }
        'logs' { & docker @ComposeArgs logs --tail 80 create-site backend }
        'demo' {
            & docker @ComposeArgs exec -T backend bench --site frontend set-config warehouse_demo 1
            if ($LASTEXITCODE -ne 0) { throw 'Could not enable demo setup.' }
            & docker @ComposeArgs exec -T backend bench --site frontend execute warehouse_core.demo.seed
        }
        'demo-production' {
            & docker @ComposeArgs exec -T backend bench --site frontend execute warehouse_core.production_demo.seed
        }
        'verify' {
            & docker @ComposeArgs exec -T backend bench --site frontend execute warehouse_core.verify_dashboard.run
            if ($LASTEXITCODE -ne 0) { throw 'Homepage dashboard checks failed.' }
            & docker @ComposeArgs exec -T backend bench --site frontend execute warehouse_core.tutorial_media.verify
            if ($LASTEXITCODE -ne 0) { throw 'Tutorial video removal checks failed.' }
            & docker @ComposeArgs exec -T backend bench --site frontend execute warehouse_core.navigation.verify
            if ($LASTEXITCODE -ne 0) { throw 'Vector ERP navigation checks failed.' }
            & docker @ComposeArgs exec -T backend bench --site frontend execute warehouse_core.branding.verify
            if ($LASTEXITCODE -ne 0) { throw 'Product branding checks failed.' }
            & docker @ComposeArgs exec -T backend bench --site frontend execute warehouse_core.report_fixes.verify
            if ($LASTEXITCODE -ne 0) { throw 'Quality Review report checks failed.' }
            & docker @ComposeArgs exec -T backend bench --site frontend execute warehouse_core.localization.verify
            if ($LASTEXITCODE -ne 0) { throw 'Ukrainian localization checks failed.' }
            & docker @ComposeArgs exec -T backend bench --site frontend execute warehouse_core.configuration_localization.verify
            if ($LASTEXITCODE -ne 0) { throw 'Configuration localization checks failed.' }
            & docker @ComposeArgs exec -T backend bench --site frontend execute warehouse_core.verify.run
            if ($LASTEXITCODE -ne 0) { throw 'ERP workflow checks failed.' }
            & docker @ComposeArgs exec -T backend bench --site frontend execute warehouse_core.verify_client.run
            if ($LASTEXITCODE -ne 0) { throw 'Client workflow checks failed.' }
            & docker @ComposeArgs exec -T backend bench --site frontend execute warehouse_core.verify_production.run
            if ($LASTEXITCODE -ne 0) { throw 'Expected production and serialized assembly checks failed.' }
            & docker @ComposeArgs exec -T backend bench --site frontend execute warehouse_core.verify_readiness.run
            if ($LASTEXITCODE -ne 0) { throw 'Order readiness checks failed.' }
            & docker @ComposeArgs exec -T backend bench --site frontend execute warehouse_core.verify_sales_completion.run
            if ($LASTEXITCODE -ne 0) { throw 'Sales completion checks failed.' }
            & docker @ComposeArgs exec -T backend bench --site frontend execute warehouse_core.verify_nova_poshta.run
            if ($LASTEXITCODE -ne 0) { throw 'Nova Poshta integration checks failed.' }
            python scripts/check_local.py
        }
        'backup' {
            & docker @ComposeArgs exec -T backend bench --site frontend backup --with-files
            if ($LASTEXITCODE -ne 0) { throw 'Backup failed.' }
            $BackupPath = Join-Path $ProjectRoot ('backups/' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
            New-Item -ItemType Directory -Path $BackupPath -Force | Out-Null
            & docker @ComposeArgs cp 'backend:/home/frappe/frappe-bench/sites/frontend/private/backups/.' $BackupPath
            if ($LASTEXITCODE -ne 0) { throw 'Backup created in container but copying it failed.' }
            Write-Host "Backup copied to $BackupPath"
        }
    }
    if ($LASTEXITCODE -ne 0) { throw "Docker action '$Action' failed." }
} finally {
    Pop-Location
}
