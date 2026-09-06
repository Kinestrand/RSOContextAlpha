$toolRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$previousPythonPath = $env:PYTHONPATH
$previousContextHome = $env:RSO_CONTEXT_HOME

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
    & python -m rso_context @args
    exit $LASTEXITCODE
}
finally {
    $env:PYTHONPATH = $previousPythonPath
    $env:RSO_CONTEXT_HOME = $previousContextHome
}
