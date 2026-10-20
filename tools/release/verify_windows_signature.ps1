param(
    [Parameter(Mandatory)][string]$Path,
    [string]$TestCertificateThumbprint = ''
)

$ErrorActionPreference = 'Stop'
$signature = Get-AuthenticodeSignature -LiteralPath $Path
if (-not $signature.SignerCertificate) {
    throw "未找到 Windows 代码签名：$Path；$($signature.StatusMessage)"
}

if ($TestCertificateThumbprint) {
    if ($env:GITHUB_ACTIONS -ne 'true' -or $env:RUNNER_OS -ne 'Windows' -or
        $env:RUNNER_ENVIRONMENT -ne 'github-hosted') {
        throw '测试证书临时信任仅允许在 GitHub Actions Windows runner 中使用。'
    }
    if ($TestCertificateThumbprint -notmatch '^[0-9a-fA-F]{40}$' -or
        $signature.SignerCertificate.Thumbprint -ne $TestCertificateThumbprint) {
        throw "测试签名证书与配置不符：$Path"
    }
    # 仅临时信任已固定指纹的公开测试证书；仍要求 Windows 完整验签为 Valid。
    $store = [System.Security.Cryptography.X509Certificates.X509Store]::new('Root', 'CurrentUser')
    $inserted = $false
    $certificate = $signature.SignerCertificate
    try {
        $store.Open([System.Security.Cryptography.X509Certificates.OpenFlags]::ReadWrite)
        $existing = $store.Certificates.Find(
            [System.Security.Cryptography.X509Certificates.X509FindType]::FindByThumbprint,
            $TestCertificateThumbprint, $false
        )
        if ($existing.Count -eq 0) {
            $store.Add($certificate)
            $inserted = $true
        }
        $signature = Get-AuthenticodeSignature -LiteralPath $Path
        if ($signature.Status -ne 'Valid' -or
            $signature.SignerCertificate.Thumbprint -ne $TestCertificateThumbprint) {
            throw "测试签名验证失败：$Path；$($signature.Status)：$($signature.StatusMessage)"
        }
        if (-not $signature.TimeStamperCertificate) {
            throw "代码签名缺少时间戳：$Path"
        }
    } finally {
        try {
            if ($inserted) { $store.Remove($certificate) }
        } finally {
            $store.Close()
        }
    }
} else {
    if ($signature.Status -ne 'Valid') {
        throw "正式签名验证失败：$Path；$($signature.Status)：$($signature.StatusMessage)"
    }
    $publisher = $signature.SignerCertificate.GetNameInfo(
        [System.Security.Cryptography.X509Certificates.X509NameType]::SimpleName, $false
    )
    if ($publisher -ne 'SignPath Foundation') {
        throw "正式签名发布者不符：$Path；$publisher"
    }
}

if (-not $signature.TimeStamperCertificate) {
    throw "代码签名缺少时间戳：$Path"
}
Write-Host "Windows 签名已验证：$Path；状态=$($signature.Status)；证书=$($signature.SignerCertificate.Thumbprint)"
