$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

Add-Type -AssemblyName System.Windows.Forms
$cookie = [System.Windows.Forms.Clipboard]::GetText()
if ([string]::IsNullOrWhiteSpace($cookie)) {
  Write-Host "剪贴板为空。请先复制 Network 请求里的完整 cookie 值。"
  exit 1
}

# 允许用户复制整行 "cookie: xxx" 或纯 cookie 值
$cookie = $cookie -replace '(?i)^cookie\s*:\s*', ''
$cookie = $cookie.Trim()

if ($cookie -notmatch 'sessionid=') {
  Write-Host "警告: 内容里没看到 sessionid= ，可能不是完整登录 Cookie。"
}

$envPath = Join-Path (Get-Location) ".env"
if (-not (Test-Path $envPath)) {
  Copy-Item ".env.example" ".env"
}

$lines = Get-Content $envPath -Encoding UTF8
$found = $false
$out = foreach ($line in $lines) {
  if ($line -match '^DOUBAO_COOKIE=') {
    $found = $true
    "DOUBAO_COOKIE=$cookie"
  } else {
    $line
  }
}
if (-not $found) {
  $out += "DOUBAO_COOKIE=$cookie"
}
$out | Set-Content $envPath -Encoding UTF8
Write-Host "已写入 DOUBAO_COOKIE 到 .env"
Write-Host ("长度: {0}" -f $cookie.Length)