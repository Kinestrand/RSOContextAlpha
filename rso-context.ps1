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
    if ($args.Count -ge 1 -and $args[0] -eq "mcp") {
        $runtimePy = Join-Path $toolRoot "mcp-runtime\Scripts\python.exe"
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
