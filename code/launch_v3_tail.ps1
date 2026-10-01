$psi = New-Object System.Diagnostics.ProcessStartInfo
$psi.FileName = "python"
$psi.Arguments = "C:\Users\DFET\.openclaw\workspace\v3_log_tail.py"
$psi.UseShellExecute = $false
$psi.CreateNoWindow = $false
$psi.WindowStyle = "Normal"
$proc = [System.Diagnostics.Process]::Start($psi)
Write-Host "v3_log_tail PID=$($proc.Id)"