#Requires -Version 5.1
[CmdletBinding()]
param(
    [ValidatePattern('^[a-zA-Z0-9_.][a-zA-Z0-9_.-]*@[a-zA-Z0-9][a-zA-Z0-9.-]*$')]
    [string]$SshTarget = 'kunetska@192.168.1.106',
    [switch]$Force
)

$ErrorActionPreference = 'Stop'
$ssh = Get-Command ssh -CommandType Application -ErrorAction Stop
$accountId = (Read-Host 'Cloudflare Account ID').Trim()
if ($accountId -notmatch '\A[0-9a-fA-F]{32}\z') {
    throw 'Expected a 32-character hexadecimal Cloudflare Account ID.'
}

# Only fixed commands travel in the SSH arguments. Credentials travel via stdin.
$install = 'ln -T -- $tmp .config/sanya-image-test.env'
if ($Force) {
    $install = 'mv -fT -- $tmp .config/sanya-image-test.env'
}
$remote = 'set -eu; umask 077; cd; mkdir -p .config; tmp=$(mktemp .config/.sanya-image-test.XXXXXXXX); trap ''rm -f -- $tmp'' EXIT; tr -d ''\015'' > $tmp; chmod 600 -- $tmp; ' + $install

$secret = Read-Host 'Cloudflare Workers AI API Token' -AsSecureString
$ptr = [IntPtr]::Zero
try {
    $ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secret)
    $token = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr).Trim()
    if ($token -notmatch '\A[A-Za-z0-9_-]+\z') {
        throw 'Expected a Cloudflare API token without quotes or spaces.'
    }

    $OutputEncoding = [Text.Encoding]::ASCII
    "CLOUDFLARE_ACCOUNT_ID=$accountId", "CLOUDFLARE_API_TOKEN=$token" |
        & $ssh.Source -T -o BatchMode=yes -o ConnectTimeout=10 $SshTarget $remote
    if ($LASTEXITCODE -ne 0) {
        throw 'Transfer failed. Check SSH access. If the test file already exists, use -Force only to replace it intentionally.'
    }
} finally {
    Remove-Variable token -ErrorAction SilentlyContinue
    if ($ptr -ne [IntPtr]::Zero) {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr)
    }
    $secret.Dispose()
}

Write-Host 'Saved ~/.config/sanya-image-test.env (mode 600). Bot configuration was not changed.'
