# Read-only Windows diagnostics only. External Docker/WSL daemon work still
# requires its own deadline; the job contains this worker and its descendants.
function Initialize-DiagnosticLimits {
    if ('Ruisheng.Diagnostics.BoundedJob' -as [type]) { return }
    Add-Type -TypeDefinition @'
using System;
using System.ComponentModel;
using System.Diagnostics;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
namespace Ruisheng.Diagnostics {
    public sealed class Capture {
        readonly StringBuilder text = new StringBuilder();
        int exceeded;
        public bool Exceeded { get { return Volatile.Read(ref exceeded) != 0; } }
        public Task Completion { get; private set; }
        public string Text { get { lock(text) { return text.ToString(); } } }
        public Capture(StreamReader reader, int limit) {
            Completion = Task.Run(() => {
                char[] buffer = new char[4096]; int count;
                while ((count = reader.Read(buffer, 0, buffer.Length)) > 0) {
                    lock(text) {
                        int retained = Math.Min(count, Math.Max(0, limit - text.Length));
                        text.Append(buffer, 0, retained);
                        if (retained != count) Interlocked.Exchange(ref exceeded, 1);
                    }
                }
            });
        }
    }
    public sealed class BoundedJob : IDisposable {
        [StructLayout(LayoutKind.Sequential)] struct Basic {
            public long ProcessTime, JobTime; public uint Flags;
            public UIntPtr Minimum, Maximum; public uint ActiveLimit;
            public UIntPtr Affinity; public uint Priority, Scheduling;
        }
        [StructLayout(LayoutKind.Sequential)] struct Limits {
            public Basic Basic; public ulong ReadOps, WriteOps, OtherOps, ReadBytes, WriteBytes, OtherBytes;
            public UIntPtr ProcessMemory, JobMemory, PeakProcess, PeakJob;
        }
        [StructLayout(LayoutKind.Sequential)] struct Accounting {
            public long UserTime, KernelTime, PeriodUserTime, PeriodKernelTime;
            public uint PageFaults, TotalProcesses, ActiveProcesses, TerminatedProcesses;
        }
        [DllImport("kernel32.dll", SetLastError=true)] static extern IntPtr CreateJobObject(IntPtr attributes, string name);
        [DllImport("kernel32.dll", SetLastError=true)] static extern bool SetInformationJobObject(IntPtr job, int kind, ref Limits value, uint length);
        [DllImport("kernel32.dll", SetLastError=true)] static extern bool QueryInformationJobObject(IntPtr job, int kind, out Accounting value, uint length, IntPtr returned);
        [DllImport("kernel32.dll", SetLastError=true)] static extern bool QueryInformationJobObject(IntPtr job, int kind, out Limits value, uint length, IntPtr returned);
        [DllImport("kernel32.dll", SetLastError=true)] static extern bool AssignProcessToJobObject(IntPtr job, IntPtr process);
        [DllImport("kernel32.dll", SetLastError=true)] static extern bool IsProcessInJob(IntPtr process, IntPtr job, out bool result);
        [DllImport("kernel32.dll", SetLastError=true)] static extern bool TerminateJobObject(IntPtr job, uint code);
        [DllImport("kernel32.dll")] static extern bool CloseHandle(IntPtr handle);
        IntPtr handle;
        public BoundedJob(ulong memoryBytes) {
            handle = CreateJobObject(IntPtr.Zero, null);
            if (handle == IntPtr.Zero) throw new Win32Exception();
            Limits limits = new Limits();
            // Kill descendants when the supervisor disappears; cap tree commit.
            limits.Basic.Flags = 0x2000 | 0x200;
            limits.JobMemory = new UIntPtr(memoryBytes);
            if (!SetInformationJobObject(handle, 9, ref limits, (uint)Marshal.SizeOf(typeof(Limits)))) {
                Dispose(); throw new Win32Exception();
            }
        }
        public void Assign(Process process) {
            bool assigned;
            if (!AssignProcessToJobObject(handle, process.Handle) ||
                !IsProcessInJob(process.Handle, handle, out assigned) || !assigned)
                throw new Win32Exception();
        }
        public ulong PeakMemory {
            get {
                Limits value;
                if (!QueryInformationJobObject(handle, 9, out value, (uint)Marshal.SizeOf(typeof(Limits)), IntPtr.Zero))
                    throw new Win32Exception();
                return value.PeakJob.ToUInt64();
            }
        }
        public bool WaitForEmpty(int milliseconds) {
            Stopwatch clock = Stopwatch.StartNew();
            do {
                Accounting value;
                if (!QueryInformationJobObject(handle, 1, out value, (uint)Marshal.SizeOf(typeof(Accounting)), IntPtr.Zero))
                    throw new InvalidOperationException("diagnostic_cleanup_uncertain");
                if (value.ActiveProcesses == 0) return true;
                Thread.Sleep(20);
            } while(clock.ElapsedMilliseconds < milliseconds);
            return false;
        }
        public void StopAndConfirm() {
            if (!TerminateJobObject(handle, 124) || !WaitForEmpty(5000))
                throw new InvalidOperationException("diagnostic_cleanup_uncertain");
        }
        public void Dispose() {
            if (handle != IntPtr.Zero) { CloseHandle(handle); handle = IntPtr.Zero; }
        }
    }
}
'@
}

function Invoke-BoundedDiagnostic {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][string]$Script,
        [ValidateRange(1,300)][int]$TimeoutSeconds=120,
        [ValidateRange(128,512)][int]$MemoryLimitMb=512,
        [ValidateRange(1024,4194304)][int]$MaxOutputChars=2097152
    )
    $ErrorActionPreference='Stop'
    if ($Script.Length -gt 1048576) { throw 'diagnostic_input_exceeded' }
    Initialize-DiagnosticLimits
    # The worker blocks on stdin until job assignment has succeeded.
    $bootstrap=@'
$ErrorActionPreference='Stop'
$ProgressPreference='SilentlyContinue'
[Console]::InputEncoding=New-Object Text.UTF8Encoding($false)
[Console]::OutputEncoding=New-Object Text.UTF8Encoding($false)
$text=[Console]::In.ReadToEnd().TrimStart([char]0xFEFF)
if ($text.Length -gt 2097152) { exit 125 }
$source=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($text))
& ([ScriptBlock]::Create($source))
'@
    $process=New-Object Diagnostics.Process
    $process.StartInfo=New-Object Diagnostics.ProcessStartInfo
    $process.StartInfo.FileName=(Get-Process -Id $PID).Path
    $process.StartInfo.Arguments='-NoLogo -NoProfile -NonInteractive -EncodedCommand '+[Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($bootstrap))
    $process.StartInfo.UseShellExecute=$false
    $process.StartInfo.CreateNoWindow=$true
    $process.StartInfo.RedirectStandardInput=$true
    $process.StartInfo.RedirectStandardOutput=$true
    $process.StartInfo.RedirectStandardError=$true
    $process.StartInfo.StandardOutputEncoding=[Text.UTF8Encoding]::new($false)
    $process.StartInfo.StandardErrorEncoding=[Text.UTF8Encoding]::new($false)
    $job=[Ruisheng.Diagnostics.BoundedJob]::new([uint64]$MemoryLimitMb * 1MB)
    $clock=[Diagnostics.Stopwatch]::StartNew()
    $started=$false; $assigned=$false
    try {
        $previous=[Console]::InputEncoding
        [Console]::InputEncoding=[Text.UTF8Encoding]::new($false)
        try { $started=$process.Start() } finally { [Console]::InputEncoding=$previous }
        if (-not $started) { throw 'diagnostic_start_failed' }
        $job.Assign($process); $assigned=$true
        $stdout=[Ruisheng.Diagnostics.Capture]::new($process.StandardOutput,$MaxOutputChars)
        $stderr=[Ruisheng.Diagnostics.Capture]::new($process.StandardError,$MaxOutputChars)
        $bytes=[Text.Encoding]::ASCII.GetBytes([Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($Script)))
        $write=$process.StandardInput.BaseStream.WriteAsync($bytes,0,$bytes.Length)
        while (-not $write.Wait(100)) {
            if ($clock.Elapsed.TotalSeconds -ge $TimeoutSeconds) { throw 'diagnostic_timeout' }
        }
        [void]$write.GetAwaiter().GetResult()
        $process.StandardInput.Close()
        while (-not $process.WaitForExit(100)) {
            if ($stdout.Exceeded -or $stderr.Exceeded) { throw 'diagnostic_output_exceeded' }
            if ($clock.Elapsed.TotalSeconds -ge $TimeoutSeconds) { throw 'diagnostic_timeout' }
        }
        if (-not $job.WaitForEmpty(250)) { throw 'diagnostic_descendant_left_running' }
        if (-not [Threading.Tasks.Task]::WaitAll(@($stdout.Completion,$stderr.Completion),5000)) {
            throw 'diagnostic_stream_timeout'
        }
        if ($stdout.Exceeded -or $stderr.Exceeded) { throw 'diagnostic_output_exceeded' }
        if ($process.ExitCode -ne 0) { throw 'diagnostic_worker_failed' }
        if ($stderr.Text) { throw 'diagnostic_worker_stderr' }
        return [pscustomobject]@{output=$stdout.Text;elapsed_ms=$clock.ElapsedMilliseconds;peak_job_memory_bytes=$job.PeakMemory}
    } finally {
        try {
            if ($assigned) {
                if (-not $job.WaitForEmpty(250)) { $job.StopAndConfirm() }
            } elseif ($started -and -not $process.HasExited) {
                $process.Kill(); [void]$process.WaitForExit(5000)
            }
        } finally { $job.Dispose(); $process.Dispose() }
    }
}

function New-BoundedDiagnosticScript {
    param([Parameter(Mandatory)][string]$Script,[ValidateRange(1,300)][int]$TimeoutSeconds=120)
    $definitions=[IO.File]::ReadAllText((Join-Path $PSScriptRoot 'diagnostic_limits.ps1'))
    $encoded=[Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($Script))
    $invoke=@'
try {
    $source=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('__SOURCE__'))
    $run=Invoke-BoundedDiagnostic -Script $source -TimeoutSeconds __TIMEOUT__
    @{ok=$true;output=$run.output;elapsed_ms=$run.elapsed_ms;peak_job_memory_bytes=$run.peak_job_memory_bytes} | ConvertTo-Json -Compress
} catch {
    $code=[string]$_.Exception.Message
    if ($code -notmatch '^diagnostic_[a-z_]+$') { $code='diagnostic_supervisor_failed' }
    @{ok=$false;error=$code} | ConvertTo-Json -Compress
}
'@
    return $definitions+"`n"+$invoke.Replace('__SOURCE__',$encoded).Replace('__TIMEOUT__',[string]$TimeoutSeconds)
}
