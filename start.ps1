[CmdletBinding(DefaultParameterSetName = "Default")]
param(
    [switch]$DebugPrompts,
    [Parameter(Mandatory = $true, ParameterSetName = "Resume")]
    [switch]$ResumePending,
    [Parameter(Mandatory = $true, ParameterSetName = "Discard")]
    [switch]$DiscardPending
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
    # [2026-07-20] resume 与 discard 由 ParameterSet 在读取凭据和启动 Python 前互斥。
    if ($ResumePending) {
        $chatArgs += "--resume-pending"
    }
    if ($DiscardPending) {
        $chatArgs += "--discard-pending"
    }
    & ".\.venv\Scripts\python.exe" @chatArgs
} finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($keyPointer)
    Remove-Item Env:\DEEPSEEK_API_KEY -ErrorAction SilentlyContinue
}
