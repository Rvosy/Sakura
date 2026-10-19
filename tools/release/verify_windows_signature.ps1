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
    if ($signature.SignerCertificate.Thumbprint -ne $TestCertificateThumbprint) {
        throw "测试签名证书与配置不符：$Path"
    }
    # 测试证书不受 Windows 公共根信任；仍拒绝内容被改动等验签错误。
    if ($signature.Status -notin @('Valid', 'NotTrusted')) {
        throw "测试签名验证失败：$Path；$($signature.Status)：$($signature.StatusMessage)"
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
