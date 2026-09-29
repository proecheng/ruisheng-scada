# Serial collection and availability repair

The site confirmed COM3, 9600/8N1, slave 1, FC3, start 0 and 38 registers with a valid XCOM request/response. After releasing COM3, the gateway now collects 38 raw values every five seconds. `ADDR-001` is an address label, not a verified manufacturer model. Engineering scaling in the supplied point document remains ambiguous, so all 38 points explicitly show raw values without engineering units or alarm thresholds.

The Windows attach task previously selected a stale ttyUSB0 with the same FTDI serial as the live ttyUSB1. Selection now checks the active VHCI device identifier for the current usbipd bus before matching USB identity. The task also preserves root-directory traversal permissions and sends BOM-free UTF-8 under Windows PowerShell 5.1. Three shell fixtures cover the stale duplicate, absent live slot and ambiguous live slots.

Desktop, maintenance and upgrade entry points now consistently append the protected external `C:\Ruisheng\site\site-serial.override.json`. The embedded validator allows only the canonical gateway serial environment and device mapping and pins the file while in use. Historical signed candidates are unchanged. The desktop shortcut remains PowerShell 7.

The gateway previously never persisted communication status, leaving devices offline even with fresh telemetry. Serial transactions now retain send time, validated successful response time and consecutive timeout/exception count. A separate bounded task persists those observations every second, guarded by tenant, configuration version, endpoint, enabled state and deletion state. It does not increment configuration versions and skips unchanged rows. The existing database timestamp trigger remains intact.

Online requires fewer than three consecutive failed transactions and a successful response younger than max(10 seconds, three polling intervals). The API independently expires serial availability in detail/list responses and the online-only query, including when the gateway has stopped. Future timestamps are rejected. Configuration changes clear serial availability until a response to the new configuration arrives. Database schema remains 0013_serial_polling_profile.

Validation: 112 related unit tests; eight real PostgreSQL/API/RLS/serial lifecycle integration tests, including recovery, stale/future timestamps, wrong-slave frames, endpoint changes, disabled/deleted devices and cross-tenant writes; three VHCI shell fixtures; 34 hardware tool tests. Existing launcher/upgrade/override regression results are retained in the workspace evidence directory. A first integration attempt caught the existing updated_at trigger; the final test verifies unchanged configuration versions and no redundant runtime writes.

Release source also retains the previously deployed date-input fixes. Final signed release and target acceptance evidence will be recorded separately after deployment.
