# BEGIN site serial compose
# Embedded in each remote script so historical signed candidates stay immutable.
function Assert-SiteSerialPath {
  param([Parameter(Mandatory)][string]$Path)
  $item = Get-Item -LiteralPath $Path -Force -ErrorAction Stop
  if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
    throw 'site_serial_path_linked'
  }
  $acl = Get-Acl -LiteralPath $Path
  $trusted = @('S-1-5-18', 'S-1-5-32-544')
  if ($acl.GetOwner([Security.Principal.SecurityIdentifier]).Value -cnotin $trusted) {
    throw 'site_serial_owner_invalid'
  }
  $write = [Security.AccessControl.FileSystemRights]'Write,Delete,DeleteSubdirectoriesAndFiles,ChangePermissions,TakeOwnership'
  foreach ($rule in @($acl.Access)) {
    if ($rule.AccessControlType -eq 'Allow' -and ($rule.FileSystemRights -band $write) -ne 0 -and
        $rule.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value -cnotin $trusted) {
      throw 'site_serial_writer_invalid'
    }
  }
}

function Assert-SiteSerialContent {
  param([Parameter(Mandatory)][string]$Json)
  # Requiring the canonical representation also rejects duplicate keys, hidden
  # overrides, interpolation and fields that could broaden container privileges.
  try {
    $model = $Json | ConvertFrom-Json
    $ports = @($model.services.gw.environment.GW_SERIAL_PORTS | ConvertFrom-Json)
  }
  catch { throw 'site_serial_json_invalid' }
  if ($ports.Count -ne 1 -or $ports[0].port -isnot [string] -or
      $ports[0].port -cnotmatch '^/dev/ruisheng-[A-Za-z0-9._-]{1,64}$' -or
      ($ports[0].baud_rate -isnot [int] -and $ports[0].baud_rate -isnot [long]) -or
      $ports[0].baud_rate -notin @(1200,2400,4800,9600,19200,38400,57600,115200)) {
    throw 'site_serial_parameters_invalid'
  }
  $port = [string]$ports[0].port
  $baud = [string]$ports[0].baud_rate
  $expected = '{"services":{"gw":{"environment":{"GW_SERIAL_PORTS":"[{\"port\":\"' + $port +
    '\",\"baud_rate\":' + $baud + '}]"},"devices":[{"source":"' + $port +
    '","target":"' + $port + '","permissions":"rw"}]}}}'
  if ($Json.Trim() -cne $expected) { throw 'site_serial_override_invalid' }
}

function Get-SiteSerialOverride {
  $path = 'C:\Ruisheng\site\site-serial.override.json'
  $exists = $true
  try { $item = Get-Item -LiteralPath $path -Force -ErrorAction Stop }
  catch [System.Management.Automation.ItemNotFoundException] { $exists = $false }
  if ($null -ne $script:SiteSerialSnapshot) {
    if ($exists -ne $script:SiteSerialSnapshot.exists) { throw 'site_serial_configuration_changed' }
    if (-not $exists) { return '' }
    foreach ($part in @('C:\Ruisheng','C:\Ruisheng\site',$path)) { Assert-SiteSerialPath $part }
    if ((Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash -cne $script:SiteSerialSnapshot.hash) {
      throw 'site_serial_configuration_changed'
    }
    return $path
  }
  if (-not $exists) {
    $script:SiteSerialSnapshot = @{ exists = $false }
    return ''
  }
  if ($item.PSIsContainer -or $item.Length -gt 4096) { throw 'site_serial_file_invalid' }
  foreach ($part in @('C:\Ruisheng','C:\Ruisheng\site',$path)) { Assert-SiteSerialPath $part }
  $stream = [IO.File]::Open($path, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::Read)
  try {
    $json = [IO.File]::ReadAllText($path, [Text.Encoding]::UTF8)
    Assert-SiteSerialContent $json
    $hash = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash
    # Retain the read handle until process exit. Compose must read exactly the
    # validated file, including during recovery and asynchronous native calls.
    $script:SiteSerialSnapshot = @{ exists = $true; hash = $hash; guard = $stream }
  }
  catch { $stream.Dispose(); throw }
  return $path
}

function Add-SiteSerialComposeArguments {
  param([Parameter(Mandatory)][string[]]$Arguments)
  if ($Arguments[0] -cne 'compose') { return ,$Arguments }
  $path = Get-SiteSerialOverride
  if (-not $path) { return ,$Arguments }
  $index = 1
  while ($index -lt $Arguments.Count -and $Arguments[$index].StartsWith('-')) {
    if ($Arguments[$index] -cin @('-f','--file','--env-file','--project-directory','-p','--project-name','--profile','--ansi','--progress','--parallel')) {
      if ($index + 1 -ge $Arguments.Count) { throw 'site_serial_compose_arguments_invalid' }
      if ($Arguments[$index] -cin @('-f','--file') -and $Arguments[$index+1] -ieq $path) {
        throw 'site_serial_override_already_present'
      }
      $index += 2
    }
    else { throw 'site_serial_compose_option_unsupported' }
  }
  if ($index -ge $Arguments.Count) { throw 'site_serial_compose_command_missing' }
  return ,(@($Arguments[0..($index-1)]) + @('-f',$path) + @($Arguments[$index..($Arguments.Count-1)]))
}
# END site serial compose
