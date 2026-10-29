param([ValidateSet('start','init','stop','status','logs','certificate')][string]$Command='start', [switch]$Build)
$ErrorActionPreference='Stop'
Set-Location $PSScriptRoot
function Invoke-Docker {
    & docker @args
    if ($LASTEXITCODE -ne 0) { throw "Docker command failed with exit code $LASTEXITCODE" }
}
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) { throw 'Install and start Docker Desktop with Linux containers and Compose v2.' }
Invoke-Docker compose version
switch ($Command) {
    { $_ -in 'start','init' } {
        if (-not (Test-Path .env)) {
            Invoke-Docker run --rm --mount "type=bind,src=$PSScriptRoot,dst=/workspace" --workdir /workspace python:3.12-slim python scripts/init_env.py
        }
        if ($Command -eq 'init') { break }
        if ($Build) { Invoke-Docker compose build app } else { Invoke-Docker compose pull app }
        Invoke-Docker compose up -d --no-build --wait
        Write-Host 'CatProof is running at https://localhost:8443/signup'
        Write-Host 'Export the local CA with .\community.ps1 certificate and trust it manually before browser sign-in.'
    }
    'stop' { Invoke-Docker compose down }
    'status' { Invoke-Docker compose ps }
    'logs' { Invoke-Docker compose logs --tail=100 app web }
    'certificate' {
        $Certificate = & docker compose exec -T web cat /data/caddy/pki/authorities/local/root.crt
        if ($LASTEXITCODE -ne 0) { throw 'Certificate export failed.' }
        [System.IO.File]::WriteAllText((Join-Path $PSScriptRoot 'catproof-local-ca.crt'), ($Certificate -join "`n") + "`n", [System.Text.Encoding]::ASCII)
        Write-Host 'Exported catproof-local-ca.crt. Follow docs/INSTALL.md to configure browser trust.'
    }
}
