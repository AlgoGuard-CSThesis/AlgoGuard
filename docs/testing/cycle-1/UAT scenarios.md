# AlgoGuard test cycle 1 — User acceptance scenarios (UAT-01)

**For:** Pauleen Joy O. Gomez, Product Owner (card UAT-01, test case TC-15)
**Build:** `AlgoGuard-CSThesis/AlgoGuard` main, final build `c775d13` with detection model deployment #2 (written for product code `940e082`; dry run below on `213d0be`) · **Where:** PC A, app at http://127.0.0.1:5000 in cloud mode

You run these four scenarios yourself and decide whether AlgoGuard is acceptable. Each scenario says what to do and what you should see. Tick **Pass** or **Fail**, write anything surprising under *Notes*, and sign. A failed step is a useful result: note what you saw and carry on with the next scenario.

**Before you start**
- Sign in yourself. Don't write passwords anywhere, and keep e-mail addresses out of screenshots.
- Times are shown in UTC, 8 hours behind the PC clock; every page now says so (DEF-16, fixed).

---

## Scenario 1 — An analyst spots attacks in replayed traffic

**You sign in as:** an analyst account.

1. Click **Live Monitor**.
2. Choose **Traffic source:** *Sample replay (labelled flows)* · **Sample dataset:** *Sample traffic – 5,000 flows* · **Replay speed:** *Fast* · **Flow order:** *Sequential* · **Storage:** *Save attacks*.
3. Click **Start Monitoring** and watch for about **2 minutes**, then click **Stop**.

**You should see**
- Flows appearing in **Recent Flows**, each marked **Attack** or **Normal** with a confidence.
- **Attacks** and **Alerts Queued** going up together; most flows in this sample are attacks.
- **Pending upload** returning to **0** and **Synced to cloud** matching *Queued for upload* shortly after you stop.
- No error message.

Result: ☐ Pass ☐ Fail   Notes: ______________________________________________

---

## Scenario 2 — Review an alert: acknowledge it, then resolve it

**You sign in as:** the same analyst account.

1. Click **Alerts**. Pick one alert with status **Open** and note its number.
2. Click **Acknowledge** on that alert.
3. Click **Resolve** on the same alert.
4. Click **Logs** and look at the newest entries.

**You should see**
- After step 2, the alert's status changes to **Acknowledged**.
- After step 3, it changes to **Resolved** and a **Reopen** button appears instead of *Resolve*.
- Each click asks for nothing unexpected and happens once (clicking quickly should not record it twice).
- In **Logs**, an entry for each review action you took.

Result: ☐ Pass ☐ Fail   Notes: ______________________________________________

---

## Scenario 3 — Turn alerts into a report and read it

**You sign in as:** the same analyst account.

1. On **Alerts**, tick the boxes of **three** alerts and click **Create report from selected**.
2. Click **Reports**. Open the report you just created.
3. Compare it with the three alerts you picked.

**You should see**
- The new report at the top of **Operational Reports**, with its type, the time it was generated (UTC), its owner and its installation.
- The report page says **3 alerts of evidence** and lists the three alerts you picked, with their severity and confidence.
- A **JSON** button that downloads the same report as a file.

Result: ☐ Pass ☐ Fail   Notes: ______________________________________________

---

## Scenario 4 — An administrator approves a new member of an installation

**Needs preparing first (Lead Architect):** a test analyst account that has signed in on an installation where it is not yet approved, so that it appears as **Awaiting approval**. **You sign in as:** the Administrator account.

1. Click **Nodes**.
2. Under the pending requests, find the test analyst and click **Approve**.
3. Ask the test analyst to refresh **Live Monitor** on their installation.

**You should see**
- Before approval, the test analyst's page says **Awaiting approval** and monitoring is not available.
- After you click **Approve**, the member's status shows **Approved**.
- The test analyst can start monitoring **without restarting** the app.

If the test account could not be prepared, tick *Not run* and say so.

Result: ☐ Pass ☐ Fail ☐ Not run   Notes: ______________________________________________

---

## Dry run by the test team (2026-10-20, final build 213d0be)

Scenarios 1–3 were run once on PC A, signed in as the PC A analyst, so you can see what to expect. They do not replace your own run or your decision; tick the boxes above from what you see yourself.

**Since then (2026-10-23/24):** the detection model was replaced (deployment #2, which fixes DEF-04 to DEF-07), so please run scenario 1 yourself on the final build. The test team's replay on `c775d13` (2026-10-24 05:07) showed 297 flows, 208 Attack / 89 Normal, 208 alerts queued and synced, 0 pending, no error. Scenarios 2 and 3 use alert and report pages that did not change.

| Scenario | Result of the dry run | Evidence |
|---|---|---|
| 1 | 169 flows, 123 Attack / 46 Normal, 123 alerts queued and synced, 0 pending, no error | `test-results/ui_20261020/uat/uat_s1_replay.png` |
| 2 | Alert #4633: Open → Acknowledged → Resolved, Reopen shown; one log entry per action (#5671, #5672). Rapid double-clicking was not tried | `uat_s2_alerts.png`, `uat_s2_logs.png` |
| 3 | Report #1 from alerts #4631–#4633: "3 alerts of evidence", the same three alerts, JSON download works | `uat_s3_reports.png`, `uat_s3_report_detail.png` |
| 4 | Not run: the Administrator account and a test analyst awaiting approval were not prepared | — |

## Acceptance decision

Defects open at the time of UAT are listed in `docs/testing/cycle-1/defects.md` and on the Testing Kanban (DEF cards). On 2026-10-23/24 the seven defects deferred in cycle 1 were fixed and re-tested: DEF-04 to DEF-07 (detection quality), DEF-11 (throughput) and DEF-17 (live lag) are closed. Two remain open and need your written decision:

- **DEF-19 (High):** in live capture, about 8 in 10 ordinary flows on a home PC are labelled Attack - the model learned from a lab dataset that does not look like real Wi-Fi traffic. Replay and PCAP results are reliable; live verdicts should be treated as unverified. The Master Test Plan (section 8) expects this and moves it to Iteration 6, but its exit criteria allow no open High defect, so accepting the build means accepting DEF-19 as a known limitation for Iteration 6.
- **DEF-10 (Medium):** uploads to the cloud usually take under 2 s but occasionally up to about 7 s; nothing is lost or duplicated. Accepting means accepting this as known limit L2 (unless the Lead Architect schedules an upload change).

☐ **Accept** the build for this cycle   ☐ **Accept with conditions** (write them below)   ☐ **Reject**

Conditions / reasons: ___________________________________________________________

Product Owner: **Pauleen Joy O. Gomez**   Signature: ________________________   Date: ____________
