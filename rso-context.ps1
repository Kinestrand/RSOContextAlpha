$toolRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$previousPythonPath = $env:PYTHONPATH
$previousContextHome = $env:RSO_CONTEXT_HOME
$previousInRuntime = $env:RSO_MCP_IN_RUNTIME

try {
    if (-not $env:RSO_CONTEXT_HOME) {
        $env:RSO_CONTEXT_HOME = Join-Path $env:USERPROFILE ".rso-context"
    }
    if ($previousPythonPath) {
        $env:PYTHONPATH = "$toolRoot\src;$previousPythonPath"
    }
    else {
        $env:PYTHONPATH = "$toolRoot\src"
    }
    $python = "python"
    if ($args.Count -ge 1 -and $args[0] -eq "mcp" -and $args -notcontains "--install-runtime") {
        Remove-Item Env:RSO_MCP_IN_RUNTIME -ErrorAction SilentlyContinue
        $runtimeRoot = Join-Path $toolRoot "mcp-runtime"
        if ($env:RSO_MCP_RUNTIME) {
            $runtimeRoot = $env:RSO_MCP_RUNTIME
        }
        $runtimePy = Join-Path $runtimeRoot "Scripts\python.exe"
        if ($env:RSO_MCP_RUNTIME -and -not (Test-Path $runtimePy -PathType Leaf)) {
            [Console]::Error.WriteLine("RSO_MCP_RUNTIME has no Windows Python: $runtimePy")
            exit 1
        }
        if (Test-Path $runtimePy) {
            $python = $runtimePy
            $env:RSO_MCP_IN_RUNTIME = "1"
        }
    }
    & $python -X utf8 -m rso_context @args
    exit $LASTEXITCODE
}
finally {
    $env:PYTHONPATH = $previousPythonPath
    $env:RSO_CONTEXT_HOME = $previousContextHome
    if ($null -eq $previousInRuntime) {
        Remove-Item Env:RSO_MCP_IN_RUNTIME -ErrorAction SilentlyContinue
    }
    else {
        $env:RSO_MCP_IN_RUNTIME = $previousInRuntime
    }
}
