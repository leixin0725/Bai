param(
    [switch]$DebugPrompts
)

Set-Location $PSScriptRoot

$secureKey = Read-Host "请输入 DeepSeek API Key" -AsSecureString
$keyPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureKey)

try {
    $env:DEEPSEEK_API_KEY =
        [Runtime.InteropServices.Marshal]::PtrToStringBSTR($keyPointer)

    $chatArgs = @(
        "-m", "bai_agent",
        "--config-dir", "config",
        "--data-dir", "data",
        "chat"
    )
    # [2026-07-20] 调试开关只透传给当前 chat 进程，不写环境或配置。
    if ($DebugPrompts) {
        $chatArgs += "--debug-prompts"
    }
    & ".\.venv\Scripts\python.exe" @chatArgs
} finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($keyPointer)
    Remove-Item Env:\DEEPSEEK_API_KEY -ErrorAction SilentlyContinue
}
