# deploy.ps1 -- copy every deck app into the Tulip's /user partition.
#
#   ./deploy.ps1                 # auto-detect the Tulip (mpremote resume)
#   ./deploy.ps1 -Port COM7      # or name the serial port explicitly
#
# /user survives tulip.upgrade(), so these apps stick around across firmware
# updates. Run this whenever you change a file in deck/.

param([string]$Port = "")

$connect = if ($Port) { "connect $Port resume" } else { "resume" }
$files = Get-ChildItem -Path $PSScriptRoot -Filter *.py | Sort-Object Name

foreach ($f in $files) {
    Write-Host "-> /user/$($f.Name)"
    $args = $connect.Split(" ") + @("fs", "cp", $f.FullName, ":/user/$($f.Name)")
    & python -m mpremote @args | Out-Null
    if (-not $?) { Write-Host "   FAILED"; }
}
Write-Host "Done. Reboot the Tulip (or run('home')) to see changes."
