# AlgoGuard analyst guide

This guide is for people who use AlgoGuard to watch network traffic on their own Windows
computer. It covers installing, signing in, monitoring, what happens to your records, and what
to do when something goes wrong. You never train models and you never need a secret key: your
administrator gives you an account and two public settings, and the maintainer publishes the
model.

AlgoGuard watches traffic and classifies each connection ("flow") as Normal or Attack. It never
blocks traffic and never stores the contents of packets, only facts about each flow such as its
addresses, ports, sizes and timing. **Capture only on networks you are allowed to monitor.**

## 1. What you need

- Windows 10 or 11, 64-bit, with an internet connection.
- **Python 3.14.6 (64-bit).** The model only loads with this exact version. Download it from
  <https://www.python.org/downloads/release/python-3146/> and install it with the default
  options.
- **Npcap**, only if you want to capture live traffic: <https://npcap.com>, installed with the
  default options. Replaying recorded files and samples works without it.
- From your administrator: your **e-mail address and password**, the project **URL** (starts
  with `https://`) and the **publishable key** (starts with `sb_publishable_`). The URL and key
  are public values; they are not passwords.

## 2. Install (once per computer)

1. Unzip the AlgoGuard package to a folder you own, for example `C:\AlgoGuard`. Do not run it
   from a USB stick or a network drive.
2. Double-click **`SETUP_ALGOGUARD.cmd`**. It:
   - checks for Python 3.14.6 and tells you if it is missing;
   - installs AlgoGuard's Python packages into the `venv` folder (a few minutes the first time);
   - creates the settings file `.env` and opens it in Notepad.
3. In Notepad, fill in the two lines and save:

   ```ini
   NEXT_PUBLIC_SUPABASE_URL=https://<your project>.supabase.co
   NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY=sb_publishable_<your key>
   ```

   Close Notepad. Setup checks the file and says **Settings OK** when it is right. Never put a
   password, a secret key (`sb_secret_...`) or a service key in this file; AlgoGuard refuses to
   start with one.

Setup never trains a model and never creates a database. You can run it again at any time, for
example after an update.

## 3. Start AlgoGuard and sign in

1. Double-click **`START_ALGOGUARD.cmd`**. Npcap installed with its default options lets
   AlgoGuard capture without administrator rights; if the Live Monitor lists live capture as
   unavailable, close the window and start it again with right-click, **Run as administrator**.
2. Wait for `Running on http://127.0.0.1:5000`, then open that address in your browser.
   AlgoGuard is only reachable from this computer.
3. Sign in with your e-mail address and password.

Keep the window open while you work; closing it stops AlgoGuard. **Every start needs an online
sign-in.** AlgoGuard does not keep you signed in across restarts, and it cannot sign in while
the internet is down.

While you are signed in, AlgoGuard renews your sign-in in the background, including after the
computer wakes from sleep. If it cannot (your account was deactivated, or the cloud refused
it), you are asked to sign in again. Records waiting to upload are kept.

## 4. First sign-in: get this computer approved

The first time you sign in on a computer, AlgoGuard registers it as an **installation** (also
called a node) and asks for approval. Until an administrator approves it, the Dashboard shows
**Awaiting approval** and monitoring and uploads are refused.

1. Open the **Dashboard** and copy the **installation ID** (a long code such as
   `d535e2fd-...`).
2. Send it to your administrator. They approve your account on that installation on the
   **Nodes** page.
3. Reload the Dashboard. It shows **Node approved**; no restart is needed. Records you saved
   before approval start uploading within a minute.

The installation's identity is stored in the `.algoguard` folder inside AlgoGuard. Do not copy,
move, rename or share that folder: a different folder is a different installation that needs
approval again, and records waiting in it would stay behind.

## 5. Monitor traffic

Open **Live Monitor**, choose a source and how much to save, and press **Start Monitoring**.

| Source | What it does |
|---|---|
| **Sample replay (labelled flows)** | Replays one of the bundled UNSW-NB15 samples (5,000, 10,000 or 20,000 flows). Each row keeps its true label, so the feed shows whether each verdict was right. Good for demonstrations. |
| **Recorded packets (PCAP)** | Replays a `.pcap`, `.pcapng` or `.cap` file that you put in the `captures` folder. Packets are grouped into flows exactly as in live capture. There is no true label, so **Actual** shows a dash. |
| **Live interface capture** | Watches the network card you select, in real time. Needs Npcap (and, if Npcap was installed for administrators only, AlgoGuard started as administrator). A flow is classified when its connection closes, after 15 seconds of silence, or every 120 seconds for long connections, so the first rows appear a few seconds after Start. |

Replay speed and order apply to the two replay sources only. **Pause**, **Resume** and **Stop**
control the session; reloading the page reconnects to a session that is still running. Only
one session runs at a time. AlgoGuard leaves its own connections (the browser page and its
uploads to the cloud) out of live capture.

**How much is saved:**

- **Session only**: nothing is stored; you only see the feed.
- **Save attacks** (default): every flow classified as Attack is stored with an alert.
- **Save every flow**: every classification is stored.

A session stores at most **300 records**. After that, classification carries on but further
rows are shown as **Not stored**.

**Manual Prediction** (the **Predict** page) classifies one flow you type in. The form opens
with a valid example; change a value and press **Run Prediction**.

## 6. Where your records are

Every stored record first goes into a queue on this computer and is then uploaded to the cloud
in the background, so a slow or broken connection never stops classification. Each feed row,
and each manual prediction, shows where its record is:

| Shown as | Meaning |
|---|---|
| **Session** | Classified only; not selected for storage. |
| **Memory** | Not yet saved anywhere. Becomes Pending within moments. |
| **Pending** | Saved on this computer, waiting to upload. Survives a restart. |
| **Synced** | Stored in the cloud. Only Synced means the cloud has it. |
| **Not stored** | Never saved: the session reached 300 records, or this computer's queue or disk was full. |
| **Rejected** | The cloud refused it (for example invalid values). Kept on this computer for export. |

The Dashboard's **Node and Synchronization** panel shows the totals and the **Upload status**:
*Active* (uploading normally), *Retrying* (the cloud is unreachable; it keeps trying),
*Waiting for login* or *Blocked* (see Troubleshooting).

**When the internet is down:** a monitoring session that is already running keeps classifying,
and its stored records wait as **Pending**. They upload by themselves once the connection is
back, but only while the same account is signed in on this computer: records are never uploaded
under another person's account. Starting a new session, a manual prediction, signing in and the
pages that read from the cloud (Dashboard figures, Alerts, Reports, Logs) need the connection
and show **The cloud is unavailable** until it returns.

**Limits on this computer:**

- Pending records that cannot be uploaded within **7 days** expire. The Dashboard then counts
  them as Rejected (expired).
- The queue holds a limited amount (about 50 MB of records). If it fills, new records are
  **Not stored** until uploads free space, and the Dashboard says why.
- **Export pending records** on the Dashboard downloads everything still on this computer
  (pending and rejected) as a file you can give your administrator.
- The Dashboard's **Synced** figure counts this computer's recent uploads (up to the last
  10,000). Everything ever uploaded remains in the cloud.

Logging out stops a running session. Records already queued stay on this computer and upload
after you next sign in.

## 7. Reading verdicts and attack scores

Each verdict shows its **confidence** (how sure the model is of *that* verdict) and an **attack
score**: the model's probability that the flow is an attack, as a band and a percentage.

| Band | Attack score |
|---|---|
| Low | below 50% |
| Medium | 50% to under 70% |
| High | 70% to under 90% |
| Very high | 90% and above |

A confident Normal verdict has a **Low** attack score. Read scores with these limits in mind:

- **A score is not a measure of harm or impact.** No band means "critical".
- **It is not a guaranteed likelihood.** On data the model had never seen, about 1 in 5 flows
  scored Low were in fact attacks.
- **Live traffic.** On ordinary browsing on the computer whose traffic the model was trained
  with, 3–7% of normal flows were labelled Attack. Other computers and networks may see more.
  Detection of real attacks in live traffic has not been tested. Treat live verdicts as leads to
  check, not as proof.
- **Sample replay accuracy is not a measured accuracy.** The bundled samples come from the
  model's training data.

Alerts recorded before scores existed show **Not scored** with their old severity label.

## 8. Other pages

- **Alerts**: every attack verdict that was stored, with its score and addresses. Mark alerts
  Acknowledged or Resolved, or reopen them; every change is logged. Alerts from another computer
  are **View only**: review them on the computer that recorded them.
- **Reports**: select alerts on the Alerts page (up to 250) to create a report; open it later
  on the Reports page.
- **Logs**: what AlgoGuard and its users did, newest first, with filters.
- **Nodes**: your installations and their approval.

Administrators also see:

- **Accounts**: create Analyst and Administrator accounts, change roles, deactivate accounts.
  New passwords need 12–128 characters with upper- and lower-case letters, a digit and a symbol.
- **Nodes**: approve, revoke or restore an account's access to an installation. Match requests
  by both the person and the installation ID, never by computer name alone.
- **Computers**: what each installation last reported (AlgoGuard version, model, capture state,
  and records waiting, not stored or rejected). A computer is **Stale** when nothing has arrived
  for 3 minutes; it reports only while someone is signed in to it. Administrators can see but
  not control captures.
- **Retention**: preview, then remove old records in audited batches. The suggested limits are
  30 days for flows, alerts and logs and 90 days for reports. Nothing is removed until an
  administrator runs a cleanup.

## 9. Troubleshooting

| What you see | What to do |
|---|---|
| `AlgoGuard cannot start. This installation is not set up yet.` | Run `SETUP_ALGOGUARD.cmd` and fill in the two cloud settings. |
| `Settings problem: ...` | The message names the setting. Common causes: the URL is not `https://`, the key is not a `sb_publishable_` key, or `ALGOGUARD_HOST` is not `127.0.0.1`. |
| `AlgoGuard needs Python 3.14.6` or `Models need Python 3.14.6` | Install Python 3.14.6, delete the `venv` folder, and run `SETUP_ALGOGUARD.cmd` again. |
| `This installation's Python packages differ from the model's` | Run `SETUP_ALGOGUARD.cmd` again. |
| Sign-in says the e-mail or password was not accepted | Check them, then check that Windows' clock is right: in an administrator Command Prompt run `w32tm /resync`. A clock more than 10 seconds off makes every sign-in fail. AlgoGuard has no page for changing or resetting a password: if you forgot yours, ask your administrator, and the maintainer sets a new one for you. |
| `The cloud is unavailable.` | Check the internet connection. Records wait as Pending and upload later. |
| `Your account or this installation needs administrator approval.` | Give your administrator the installation ID from the Dashboard (section 4). If you were approved before, your access may have been revoked. |
| **Model not available**: `No model is published for this project yet.` | Tell your administrator; the maintainer publishes models. |
| `Model download failed.` | Check the internet connection and reload the page. |
| `The model copy saved on this computer is corrupt.` | Close AlgoGuard, delete the folder named in the message, and start again; the model downloads afresh. |
| `The published model does not match this version of AlgoGuard.` | Ask your administrator for the current AlgoGuard package (section 10). |
| `The published model file is unavailable.` or another model message ending "tell your administrator" | Tell your administrator; this is fixed in the cloud, not on your computer. |
| Live capture is listed as unavailable | Install Npcap (the message says if it is missing), or start AlgoGuard with **Run as administrator**, then reload the Live Monitor. |
| A live session shows no rows | Pick the network card that carries your traffic. Rows appear when flows end, not on their first packet. |
| Upload status **Blocked** | Uploads were refused: this installation is not approved for you, or your access was revoked. Once the administrator approves or restores it, uploads resume by themselves within a minute; **Retry uploads** on the Dashboard resumes them at once. |
| Rows shown as **Not stored** | Expected after 300 records in a session. Otherwise the Dashboard names the cause: a full queue (wait for uploads) or a disk problem (free disk space; saving resumes by itself). |
| Records shown as **Rejected** | Use **Export pending records** and give the file to your administrator. |
| Older records have disappeared from Alerts or Logs | An administrator may have run a retention cleanup (section 8). |
| `Address already in use` / port 5000 busy | Another AlgoGuard is already running; close it. Or set `ALGOGUARD_PORT=5001` in `.env` and open `http://127.0.0.1:5001`. |

## 10. Updating and removing AlgoGuard

**Update:** close AlgoGuard, unzip the new package over the same folder (replace files when
asked), then run `SETUP_ALGOGUARD.cmd` again. Your `.env`, the `.algoguard` folder and any
records waiting to upload are kept, so the computer stays approved.

**Remove:** first make sure the Dashboard shows nothing pending, or use **Export pending
records**. Then close AlgoGuard and delete its folder. Ask your administrator to revoke the
installation on the Nodes page.

## About the sample data

The bundled samples are drawn from the UNSW-NB15 dataset by UNSW Canberra
(<https://research.unsw.edu.au/projects/unsw-nb15-dataset>). Academic work using them should
cite the dataset publications listed there.
