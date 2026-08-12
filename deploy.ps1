param([string]$msg = "deploy")

Set-Location $PSScriptRoot

Write-Host "Commitando..." -ForegroundColor Cyan
git add -A
git diff --cached --quiet
if ($LASTEXITCODE -ne 0) {
    git commit -m $msg
} else {
    git commit --allow-empty -m $msg
}

Write-Host "Subindo para GitHub..." -ForegroundColor Cyan
git push origin main

Write-Host "Fazendo deploy direto no Railway..." -ForegroundColor Cyan
railway up --service "Siqueirão Multimarcas"

Write-Host "Deploy concluido!" -ForegroundColor Green
