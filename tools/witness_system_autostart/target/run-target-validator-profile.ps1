$ErrorActionPreference = "Continue"

& pwsh.exe `
    "C:\ProgramData\Ruisheng\bin\verify-publisher.ps1" `
    "C:\Ruisheng\candidates\deploy-20260831.1" `
    -QualificationMode ValidatorProfile `
    -QualificationProfilePath "C:\ProgramData\Ruisheng\site\b08\point-profile.json" `
    -QualificationRootPath "C:\ProgramData\Ruisheng\site\b08" `
    -QualificationTrustPolicyPath `
        "C:\ProgramData\Ruisheng\site\b08\point-profile-trust-policy.json"
$code = $LASTEXITCODE
[Console]::Error.WriteLine("RUISHENG_VERIFY_EXIT_CODE=$code")
exit $code
