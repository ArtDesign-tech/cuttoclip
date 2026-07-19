[CmdletBinding()]
param(
  [Parameter(Mandatory)] [ValidatePattern('^https://')] [string]$GatewayUrl,
  [Parameter(Mandatory)] [string]$AccessClientId,
  [string]$CredentialsPath = $(Join-Path $env:LOCALAPPDATA "CutToClip\gateway-credentials.json")
)

$ErrorActionPreference = "Stop"
function Read-PlainSecret([string]$Prompt) {
  $secure = Read-Host -Prompt $Prompt -AsSecureString
  $pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
  try { return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer) }
  finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer) }
}

$accessSecret = Read-PlainSecret "Cloudflare Access client secret"
$inviteCode = Read-PlainSecret "One-time CutToClip invite code"
try {
  $headers = @{
    "CF-Access-Client-Id" = $AccessClientId
    "CF-Access-Client-Secret" = $accessSecret
  }
  $activation = Invoke-RestMethod -Method Post -Uri "$($GatewayUrl.TrimEnd('/'))/v1/activate" `
    -Headers $headers -ContentType "application/json" -Body (@{ inviteCode = $inviteCode } | ConvertTo-Json -Compress)
} catch {
  throw "Gateway activation failed. Verify the hostname, Cloudflare service credentials, and one-time invite."
} finally {
  $inviteCode = $null
}

$directory = Split-Path -Parent $CredentialsPath
New-Item -ItemType Directory -Force -Path $directory | Out-Null
$credentials = [ordered]@{
  gatewayUrl = $GatewayUrl.TrimEnd('/')
  installationToken = [string]$activation.token
  cloudflareAccessClientId = $AccessClientId
  cloudflareAccessClientSecret = $accessSecret
  requireAccess = $true
}
[System.IO.File]::WriteAllText($CredentialsPath, ($credentials | ConvertTo-Json -Compress), [System.Text.UTF8Encoding]::new($false))

$acl = New-Object System.Security.AccessControl.FileSecurity
$acl.SetAccessRuleProtection($true, $false)
$identity = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$rule = New-Object System.Security.AccessControl.FileSystemAccessRule($identity, "FullControl", "Allow")
$acl.AddAccessRule($rule)
Set-Acl -LiteralPath $CredentialsPath -AclObject $acl

$accessSecret = $null
Write-Host "Gateway activated for installation $($activation.installationId). Run scripts/start-worker.ps1 to start the local worker."
