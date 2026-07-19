Set-Location $PSScriptRoot

$secureKey = Read-Host "«Î ‰»Î DeepSeek API Key" -AsSecureString
$keyPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureKey)

try {
    $env:DEEPSEEK_API_KEY =
        [Runtime.InteropServices.Marshal]::PtrToStringBSTR($keyPointer)

    & ".\.venv\Scripts\python.exe" -m bai_agent `
        --config-dir config `
        --data-dir data `
        chat
}
finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($keyPointer)
    Remove-Item Env:\DEEPSEEK_API_KEY -ErrorAction SilentlyContinue
}