# Daily calibration round for every project with a calibration.yaml. Run by the Windows task "Arkenstone calibration".
# Safe to run twice a day: calibrate.py resumes today's batch and stops after the plan's number of rounds.
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$env:PYTHONIOENCODING = "utf-8"
New-Item -ItemType Directory -Force "$root\data\logs" | Out-Null
$log = "$root\data\logs\calibrate-$(Get-Date -Format yyyy-MM-dd).log"
foreach ($p in Get-ChildItem "$root\projects" -Directory) {
    if (Test-Path "$($p.FullName)\calibration.yaml") {
        "=== $($p.Name) $(Get-Date -Format s)" | Out-File -Append -Encoding utf8 $log
        & "$root\.venv\Scripts\python.exe" "$root\scripts\calibrate.py" --project $p.Name *>&1 | Out-File -Append -Encoding utf8 $log
    }
}
"=== done $(Get-Date -Format s)" | Out-File -Append -Encoding utf8 $log
