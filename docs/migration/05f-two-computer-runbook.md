# Stage 5F — Two-computer pilot runbook

Use this after the local checks in `05f-pilot.md`. It is written for two
separate Windows computers and the isolated hosted pilot project. It does not
need a Git commit.

This runbook was used for the 2026-10-08 to 2026-10-16 pilot, which accepted
Stage 5F with documented limits. It has since been corrected with what that
pilot found. The results are in [the Stage 5F record](05f-pilot.md).

## What this pilot should prove

Both computers run the same cloud-mode app against the same isolated Supabase
pilot, while keeping separate local node IDs, analyst accounts, model caches,
and pending-event queues. Each client should download the same verified active
model, classify its own test traffic, and upload only under its approved node
and user. The pilot also measures the real network path and records what happens
when one client temporarily loses connectivity.

Use only sample data and traffic you are authorized to monitor. AlgoGuard
classifies flow metadata; do not select a shared/public network interface for
this test. Never send a service-role key, database password, JWT signing secret,
access token, password, signed Storage URL, or `.env` file to anyone. Only the
project URL and publishable key belong in an analyst `.env`.

## 0. Provision the hosted pilot (maintainer, once)

The 2026-10-08 pilot found the hosted project empty: no migrations, no
Administrator, no Edge Function and no model. Analysts cannot fix any of that.
The maintainer does it once, on PC A, with `.env.maintainer` (never on PC B,
and never shared):

1. Apply the schema and confirm nothing is pending:

   ```powershell
   .\venv\Scripts\python.exe cloud_migrate.py --status
   .\venv\Scripts\python.exe cloud_migrate.py --dry-run
   .\venv\Scripts\python.exe cloud_migrate.py --yes
   .\venv\Scripts\python.exe cloud_migrate.py --status
   ```

2. Deploy the account-administration Edge Function. It verifies the caller's
   bearer token itself, so the gateway JWT check is turned off:

   ```powershell
   supabase functions deploy account_admin --project-ref <pilot-ref> --no-verify-jwt
   ```

3. In the dashboard, under **Authentication**, disable public sign-up and keep
   the email provider enabled.
4. Create the first Administrator. The generated password is printed once:

   ```powershell
   .\venv\Scripts\python.exe bootstrap_admin.py --email <admin email> --username <name>
   ```

5. Publish exactly one model. Only models trained after Stage 5C carry the
   runtime stamp that publication requires, so retrain first if
   `publish_model.py` refuses an older one. Pick a new UUID once and reuse it if
   you retry:

   ```powershell
   .\venv\Scripts\python.exe publish_model.py --model-id <id> --publication-id <uuid> --yes
   ```

6. Open `/simulation` as the Administrator and confirm it shows one active
   deployment.

## 1. Confirm the target before installing

1. Designate **PC A** as the coordinator and **PC B** as the second client.
   Record which is which. Plug both into power and use the network they will
   actually use for the pilot.
2. Confirm with the project maintainer that the target is the isolated Stage 5A
   hosted pilot, not production and not a local Supabase URL such as
   `127.0.0.1:54321`. The Stage 5A record identifies project
   `gswyqmznjwbonassteco` in Tokyo; confirm in the Supabase dashboard that this
   is still the intended project before using it.
3. Before either app writes data, have the maintainer confirm that the target
   has the current database migrations and exactly one compatible active model
   publication. If `/simulation` or `/monitor` reports **No active deployment**,
   stop here; an analyst installation must not publish or train a model.
4. Create two pilot-only analyst accounts from an Administrator session on the
   **Accounts** page. Give them distinct email addresses and distinct strong
   passwords. Select the **Analyst** role for both. The form requires 12–128
   characters with uppercase and lowercase letters, a digit, and a symbol.
   Keep the passwords on their respective computers; never put them in this
   runbook or the results sheet.
5. Agree on the acceptance targets before the run. At minimum, write down the
   maximum acceptable p95 upload acknowledgement delay, minimum sustained
   acknowledged events per second, maximum event-to-verdict lag, maximum packet
   drop fraction, CPU/RAM limits, and allowed local/shared storage growth. If a
   target is not yet known, write `TBD` and leave that gate open rather than
   choosing a threshold after seeing the results.
6. Synchronise both clocks before anyone signs in. In an Administrator
   PowerShell window on each PC, run `w32tm /resync`, then
   `w32tm /query /status`. If **Source** says `Local CMOS Clock`, Windows is not
   syncing: start the Windows Time service and resync. In the 2026-10-08 pilot,
   PC A's clock was 7 s slow and sign-in failed with a generic message. Since
   `940e082` the app tolerates 10 s of skew, but upload-delay figures still
   include any offset.

## 2. Put identical source code on both computers

Both PCs must run the same commit. Check with `git rev-parse --short HEAD` on
each. If PC A has uncommitted changes, cloning would test older code on PC B.
In that case, transfer PC A's current source folder over a private, trusted
method instead. Do not transfer machine-specific state or credentials. Exclude:

- `.git`, `venv`, `node_modules`, `.algoguard`, `.local`, `backups`,
  `check-output`, `database`, `saved_models`, `reports`, and `captures`;
- `.env`, `.env.*`, any maintainer environment file, local Supabase credentials,
  and any database or backup files.

Keep `app.py`, the Python modules, `requirements.txt`,
`requirements-model.lock`, `datasets`, `scripts`, `static`, `templates`, and
`.env.example`. Copy `.env.example` separately to a fresh `.env` on each PC.
That template contains no credentials. Do not copy PC A's `.algoguard` folder to
PC B: it contains the node identity and outbox that must remain independent.

For example, from PC A you can make a clean source copy on a private USB drive
(replace `E:` if its drive letter differs):

```powershell
$source = 'C:\Users\sanmi\AlgoGuard'
$usbCopy = 'E:\AlgoGuard-Pilot'
robocopy $source $usbCopy /E /XD .git venv node_modules .algoguard .local backups check-output database saved_models reports captures /XF .env .env.*
Copy-Item (Join-Path $source '.env.example') (Join-Path $usbCopy '.env.example') -Force
```

Robocopy exit codes 0–7 mean the copy completed (they distinguish copied and
skipped files); 8 or higher means a copy error. On PC B, copy that prepared
folder to its own local project directory, such as
`C:\Users\<your-user>\AlgoGuardPilot`. Do not run the app from the USB drive.
If the project is in another location on PC A, change `$source` first.

On each computer, in a PowerShell window opened in the copied project folder:

```powershell
py -3.14 --version
py -3.14 -m venv venv
.\venv\Scripts\python.exe -m pip install --upgrade pip
.\venv\Scripts\python.exe -m pip install -r requirements-dev.txt
```

Use the Python version recorded in `requirements-model.lock` (currently
Python 3.14.6). If `py -3.14 --version` is a different patch release, install
the locked version before proceeding; model loading checks runtime provenance.
`requirements-dev.txt` includes the runtime packages plus the test helper and
Ruff used in this runbook.

Edit the new `.env` **separately on each computer**. Set:

```ini
ALGOGUARD_DB_MODE=supabase
ALGOGUARD_HOST=127.0.0.1
ALGOGUARD_PORT=5000
NEXT_PUBLIC_SUPABASE_URL=https://<the-confirmed-pilot-project>.supabase.co
NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY=sb_publishable_<pilot-key>
ALGOGUARD_STATE_DIR=C:\AlgoGuardPilot\state-PC-A
```

On PC B, change only the final path to `C:\AlgoGuardPilot\state-PC-B` (or
another unique local directory). Do change it: in the 2026-10-08 pilot, PC B
kept `state-PC-A`. That was harmless because the folder sits on PC B's own disk,
but it made the records confusing. Do not rename a state folder after first
sign-in, because that gives the PC a new node identity. Keep the URL and publishable key identical on
both. The URL must be HTTPS. Never put a secret/service key in either file.
Each app intentionally serves only on `127.0.0.1`; open it on that computer at
`http://127.0.0.1:5000`. Do not expose the Flask console to the LAN.

Start AlgoGuard in an Administrator PowerShell window on both PCs, since the
live-capture test needs Npcap privileges:

```powershell
.\venv\Scripts\python.exe app.py
```

Leave each window running. If port 5000 is already occupied, stop the other
local app before starting this one; do not change `ALGOGUARD_HOST` to `0.0.0.0`.

## 3. Enroll both installations and verify separate identities

1. On PC A, open `http://127.0.0.1:5000` and sign in with the PC A analyst
   account. On PC B, do the same using the different PC B analyst account.
   First sign-in must be online. Each installation creates its own node and
   requests membership.
2. On PC A, sign out of the analyst account and sign in with the Administrator
   account. Open **Nodes and Membership**. Match each pending request by both
   analyst name and node UUID; approve the PC A analyst on PC A's node and the
   PC B analyst on PC B's node. Leave the **Default node** checkbox selected
   for each. Do not approve a request by hostname alone. Since signing in asks
   for membership on that same installation, the Administrator may also see a
   pending request for themself on PC A's node; leave that one alone unless the
   Administrator is intentionally going to run captures there.
3. Return to each PC and refresh **Nodes and Membership**. Both should show
   **Approved for you**. Record the node UUID and analyst label for each.
   Confirm the two UUIDs differ. On each PC, PowerShell can also read the local
   identity file without revealing a credential:

   ```powershell
   Get-Content C:\AlgoGuardPilot\state-PC-A\node-id
   ```

   On PC B use `Get-Content C:\AlgoGuardPilot\state-PC-B\node-id`. Change both
   paths if you chose different state folders. Record the UUIDs, not passwords
   or tokens.
4. Open **Manual Prediction** and **Live Monitor** on each PC. Verify the same
   active deployment is shown and that model loading succeeds. Each state
   directory should now contain a non-empty `models` subfolder. Do not copy
   that cache between PCs: each client must download and verify it itself.

If either user is still pending, the deployment is missing, or a node is
unapproved, resolve that before generating test writes.

## 4. Run the tests in this order

Keep a result row for each source and each client. For tests marked “together,”
start the two clients within about five seconds and write down the time between
the starts. Complete one test type on both PCs before moving to the next.

### A. Manual prediction, once on each PC

1. Open **Manual Prediction**, submit one valid flow using the form's defaults,
   and note its event UUID, result, model/deployment, latency, and **Saved**
   state.
2. Wait for the saved state to become **Synced**. `in_memory` means not yet
   durable, `durable_pending` means only on this PC, and only `synced` means the
   cloud has acknowledged it.
3. Repeat once on the other PC. Verify each event is attributed to that PC's
   analyst/node (maintainer, read-only by event UUID; see Test C). The two event
   UUIDs must differ.

### B. PCAP replay, together

Create the same small synthetic PCAP locally on each PC. This uses the project's
existing test helper; it contains generated HTTP-shaped flows and a DNS exchange,
not a recording of your network:

```powershell
New-Item -ItemType Directory -Force captures | Out-Null
@'
from pathlib import Path
from tests.test_traffic_sources import write_sample_pcap
write_sample_pcap(Path("captures/pilot-smoke.pcap"), sessions=10)
'@ | .\venv\Scripts\python.exe -
```

On both **Live Monitor** pages, choose source **PCAP**, file
`pilot-smoke.pcap`, speed **Fast**, and persistence **All**. Start them together.
Each should finish normally and show classified flows; wait until each client's
queue is fully **Synced**. Record total flows, persisted, synced, dropped,
duration, average inference latency, and any error. The PCAP is expected to
produce 11 flows (10 TCP sessions plus DNS); a different total is a finding to
investigate, not a reason to edit the test data mid-run.

### C. CSV replay and the 300-flow persistence cap, together

On both PCs, choose source **CSV**, dataset `algoguard_big.csv` (5,000 data
rows), speed **Fast**, and persistence **All**. Start together. Each should keep
classifying through the file. For one capture, at most 300 flows can be reserved
for persistence; after that, additional flows can be classified but the UI
should show that the capture is capped/dropped rather than claiming those rows
were saved. After replay, wait for the queue to drain and record each PC's
`flows`, `persisted`, `synced`, `pending`, `dropped`, completion time, and
average inference latency. Do not expect all 5,000 flows to be persisted.

Check that events from both node UUIDs exist, that each node has its own owner,
and that no row has been assigned to the other client. Ordinary pages show only
the local node until 6B.1, so the maintainer checks this read-only by capture
ID. Compare cloud-acknowledged totals with the clients' `synced` counts.

### D. Live capture and cloud-traffic exclusion, once on each PC

Use the Monitor interface dropdown to select the active Wi-Fi/Ethernet interface
used to reach the pilot. Do not select a VPN or shared network unless you have
permission to monitor it. Start source **Live**, persistence **All**, and
generate a little harmless traffic from that PC (for example, load the pilot
dashboard in a second browser tab). Stop from the Monitor page. Record packets
captured/dropped, excluded own traffic, flows, average detection lag, and final
queue state. The app should not classify its own cloud requests as unrelated
traffic.

Keep each live run well under an hour. Once a capture reaches its 300-record
cap, nothing refreshes the sign-in, so the capture stops when the access token
expires, about 1 hour after sign-in. That is Stage 5F limit L3, owned by 6C.1.

Then run the stronger same-provider check in an Administrator PowerShell window
on **each** PC, using the exact interface name shown in that PC's Monitor list:

```powershell
.\venv\Scripts\python.exe scripts\check_capture_exclusion.py --interface "<interface name>" --requests 10
```

It sends ten public JWKS requests through AlgoGuard's registered cloud opener
and ten ordinary HTTPS requests to that same host and port. This is a small,
unauthenticated read-only check. The JSON must show `passed: true`,
`owned_flows_classified: 0`, `unrelated_flows_classified` greater than zero,
and `packets_dropped: 0` (investigate if any differ). Keep the JSON output, but
remove the `host`/`remote_ips` fields if sharing it publicly. Never paste `.env`
or any request headers into the report.

### E. One-client network loss, pending writes, and restart

Do this on PC B only; leave PC A online and monitoring the pilot. First ensure
PC B is approved, signed in, and has downloaded the active model. Use the CSV
source so this outage does not depend on seeing traffic on a disconnected
network adapter.

1. **Practise disconnecting first.** Turn off the Wi-Fi tile in the taskbar's
   network panel, unplug any network cable, and confirm that a website fails to
   load. Airplane mode alone did not disconnect PC B on 2026-10-16, because
   Windows remembers Wi-Fi being switched on during Airplane mode.
   Reconnect afterwards.
2. Start **CSV** / `algoguard_biggest.csv` (20,000 data rows) / **Fast** /
   **Save every flow** on PC B **while online**: starting registers the
   capture in the cloud and is refused offline. As soon as the first rows
   appear, about 2 seconds later, disconnect PC B as in step 1.
   Do not disable PC A. AlgoGuard should continue classifying locally; records
   should become **Pending** on PC B. While nothing has uploaded yet, the
   upload status may still look normal; the cloud simply stops receiving.
   With Fast pacing, the 300-record cap is reached about 105 s after Start.
   After about 2 minutes, note **Pending upload** and press **Stop**.
3. In PC B's server PowerShell window, press **Ctrl+C** and wait for the app to
   exit; note roughly how long it takes. Leave `ALGOGUARD_STATE_DIR` untouched.
   Restart `app.py` while still offline; sign-in is expected to fail because
   login is online-only. This does not mean the outbox was lost.
4. Reconnect PC B to the Internet, then sign in with the **same PC B analyst**.
   Open the dashboard and wait for upload to recover. Pending should decrease
   to zero and become synced. Uploads resume only after this sign-in, so time
   recovery from the sign-in. The cloud's `received_at` values give the exact
   times.
   Do not delete, move, or export the state directory during this test.
5. Confirm the recovered records appear once under PC B's node and owner. The
   maintainer can check this read-only by capture ID. Compare unique
   events/receipts before and after; a retry should not create duplicates.
   PC A's node and records should be unaffected.

If the test unexpectedly reports `rejected` or `blocked`, stop and preserve the
local state directory for diagnosis; do not clear the queue or retry as another
user.

### F. Membership revocation with a still-valid login

Run this last, after both outboxes show zero pending and every test capture is
stopped. In the Administrator **Nodes and Membership** page, revoke only PC B
analyst's membership on PC B's node. Do not deactivate the account.

Without logging out on PC B, try one manual prediction and try to start a new
capture. Both should be refused for missing approved membership. Write down the
wording of each refusal. PC B should remain signed in so the blocked state and
any exportable pending data remain visible. Confirm PC A still works.

Ordinary pages, including the Administrator's Alert History, show only the
installation's own node until 6B.1. So the Administrator cannot review PC B's
records from PC A in the app. Cross-node reads are covered by the integration
tests. On the hosted pilot, they can also be checked read-only in SQL under each
account's own access; see §3 of the Stage 5F record.

Then use the **Restore** action for that exact PC B membership. On PC B, open
the **Dashboard** and press **Retry uploads**, or sign out and in again. The
earlier refusal left a local permission block, and a page refresh does not
clear it. Until then, every Start opens an empty capture in the cloud and stops
at once. Then confirm that a capture runs. Record the result.

## 5. Capture network and resource measurements

Do not tune limits until targets are written down. For each computer, record
Windows version/build, CPU model/core count, RAM, power mode, Python version,
Npcap version, network type (Wi-Fi/Ethernet), and selected interface. Record
the cloud region/project ref, but never the key.

Take ten read-only HTTPS timing samples to the pilot's public JWKS endpoint from
each PC. Replace the URL with the confirmed pilot URL; this endpoint is public
and needs no account token:

```powershell
$url = 'https://<confirmed-pilot-project>.supabase.co/auth/v1/.well-known/jwks.json'
1..10 | ForEach-Object {
    curl.exe -sS -o NUL -w '%{time_connect},%{time_starttransfer},%{time_total}\n' $url
}
```

Record connect, first-byte, and total time in milliseconds (the command prints
seconds; multiply by 1,000). Sort each column; with ten samples the nearest-rank
p95 is the largest sample, while the median is the average of samples 5 and 6.
This is an HTTP timing proxy, not pure ICMP RTT. Repeat at the start and end of
the pilot to expose variation.

During the two-client CSV run, open **Task Manager → Details** on each PC and
record AlgoGuard's Python process CPU and working-set memory at idle, peak, and
after sync. In PowerShell, measure the local state directory before and after
the run (substitute the exact configured path):

```powershell
$state = 'C:\AlgoGuardPilot\state-PC-A'
(Get-ChildItem $state -File -Recurse -ErrorAction SilentlyContinue |
    Measure-Object -Property Length -Sum).Sum / 1MB
```

For live capture, record `packets_captured`, `packets_dropped`, average
event-to-verdict detection lag, and excluded own-traffic count from the Monitor
page. When both packet counters are present, calculate observed capture drop
percentage as `100 * packets_dropped / (packets_captured + packets_dropped)`;
write `N/A` if both are zero. For upload delay, note when an event first appears
as pending and when it becomes synced. For the outage test, measure from
restoring network until the last pending event is synced. Use the UI totals; do
not infer cloud success from classification alone. The Monitor's average
inference latency is model execution time, while live detection lag includes
the flow ending and producing a verdict; keep those as separate measurements.

## 6. Results sheet and cleanup

Copy this table into your notes and fill a row for each test/client. Add a short
note for any error or unexpected status; keep screenshots only if they do not
show emails, passwords, tokens, or signed URLs.

| Test | PC | Source/condition | Flows | Synced | Pending peak/final | Dropped | Duration | Avg inference / live lag | CPU/RAM peak | Notes |
|---|---|---|---:|---:|---:|---:|---:|---|---|---|
| Manual | A | Online | | | | | | | | |
| Manual | B | Online | | | | | | | | |
| PCAP | A | Concurrent | | | | | | | | |
| PCAP | B | Concurrent | | | | | | | | |
| CSV cap | A | Concurrent | | | | | | | | |
| CSV cap | B | Concurrent | | | | | | | | |
| Live + exclusion | A | Online/Npcap | | | | | | | | |
| Live + exclusion | B | Online/Npcap | | | | | | | | |
| Offline/restart | B | Disconnect/reconnect | | | | | | | | |
| Revocation | B | Valid login, membership revoked/restored | | | | | | | | |

Also record the ten latency samples per PC, p50/p95, node UUIDs, model
manifest/deployment IDs and object SHA-256, migration/version confirmation, and
acceptance targets next to the table. Report whether the two nodes stayed
separate, whether every pending record recovered once, and the actual vs target
for every budget.

Before finishing, stop any live capture, confirm both queues show zero pending,
restore PC B membership, close the app on both PCs, and preserve both state
directories until the results are reviewed. Do not delete cloud pilot records
or rotate/deactivate accounts as ad-hoc cleanup. Send me the filled-in totals
and any sanitized error text; never send credentials, `.env`, outbox contents,
or raw flow exports. I can then help interpret the evidence and update the Stage
5F record. Keep Stage 5F open if any physical test or declared budget is still
missing.
