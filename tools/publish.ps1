# 一键发布到 GitHub
# 用法：在 PowerShell 里执行（先到 GitHub 网页建好空仓库，不要勾 README/license）
#   .\tools\publish.ps1 -User 你的GitHub用户名 -Repo ai-xiaoshuo
param(
    [Parameter(Mandatory=$true)][string]$User,
    [string]$Repo = 'ai-xiaoshuo'
)

$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

Write-Host "[1/4] 检查敏感文件是否会进版本库..." -ForegroundColor Cyan
$leak = & git ls-files | Select-String -Pattern '^data/|app\.db|node_modules|^\.env$|sk_tr_'
if ($leak) {
    Write-Host "发现不该提交的文件：" -ForegroundColor Red
    $leak
    Write-Host "先 git rm --cached <文件> 再重试。" -ForegroundColor Yellow
    exit 1
}
Write-Host "  OK：没有密钥 / 数据库 / 小说正文进入版本库" -ForegroundColor Green

Write-Host "[2/4] 配置远程地址..." -ForegroundColor Cyan
$url = "https://github.com/$User/$Repo.git"
$has = (git remote) -contains "origin"
if ($has) { git remote set-url origin $url } else { git remote add origin $url }
Write-Host "  origin = $url"

Write-Host "[3/4] 确认分支与提交..." -ForegroundColor Cyan
git branch -M main
git log --oneline -1

Write-Host "[4/4] 推送（首次会要求登录 / Token）..." -ForegroundColor Cyan
git push -u origin main

Write-Host ""
Write-Host "完成：https://github.com/$User/$Repo" -ForegroundColor Green
