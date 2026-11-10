# AlgoGuard test cycle 2 — User acceptance scenarios (C2-UAT-01)

**For:** Pauleen Joy O. Gomez, Product Owner (card C2-UAT-01, test cases TC-15 and TC-18)
**Build:** release 0.6.0, commit `8864f29` (application code of `4740a43`), detection model 60 as deployment #3 · **Where:** PC A, AlgoGuard at http://127.0.0.1:5000, connected to the hosted pilot

Part A has five scenarios for you to run yourself (TC-15). Part B is for a different person, someone who has never used AlgoGuard (TC-18). Each scenario says what to do and what you should see. Tick **Pass** or **Fail** and write anything surprising under *Notes*. A failed step is a useful result: note what you saw and carry on with the next scenario. No signature is needed; the Test Log records your results (Lead Architect decision, 2026-11-10).

**Before you start**
- Sign in yourself. Don't write passwords anywhere, and keep e-mail addresses out of screenshots.
- Times are shown in UTC, 8 hours behind the PC clock; every page says so.
- An **attack score** is the model's probability that a flow is an attack, shown as a band: **Low** (below 50%), **Medium** (50–70%), **High** (70–90%) or **Very high** (90% and above). It is not a measure of harm.

---

## Part A — Product Owner (TC-15)

### Scenario 1 — An analyst spots attacks in replayed traffic

**You sign in as:** an analyst account.

1. Click **Live Monitor**.
2. Choose **Traffic source:** *Sample replay (labelled flows)* · **Sample dataset:** *Sample traffic – 5,000 flows* · **Replay speed:** *Fast* · **Flow order:** *Sequential* · **Storage:** *Save attacks*.
3. Click **Start Monitoring**, watch for about **2 minutes**, then click **Stop**.

**You should see**
- Flows in **Recent Flows**, each marked **Attack** or **Normal**, with a confidence and an attack score band.
- Attack verdicts mostly in the **High** or **Very high** band, Normal verdicts mostly **Low**.
- **Attacks** and **Alerts Queued** going up together.
- **Pending upload** returning to **0** and every stored record **Synced** shortly after you stop.
- No error message.

Result: ☐ Pass ☐ Fail   Notes: ______________________________________________

---

### Scenario 2 — Review an alert: acknowledge it, then resolve it

**You sign in as:** the same analyst account.

1. Click **Alerts**. Pick one alert with status **Open** from this computer and note its number.
2. Click **Acknowledge** on that alert.
3. Click **Resolve** on the same alert, and click it twice quickly.
4. Click **Logs** and look at the newest entries.

**You should see**
- After step 2, the alert's status changes to **Acknowledged**.
- After step 3, it changes to **Resolved** and a **Reopen** button appears instead of *Resolve*. The double click records it once.
- The alert shows the same score band and percentage it had in the Live Monitor.
- In **Logs**, one entry for each review action you took.

Result: ☐ Pass ☐ Fail   Notes: ______________________________________________

---

### Scenario 3 — Turn alerts into a report and read it

**You sign in as:** the same analyst account.

1. On **Alerts**, tick the boxes of **three** alerts and click **Create report from selected**.
2. Click **Reports** and open the report you just created.
3. Compare it with the three alerts you picked.

**You should see**
- The new report at the top of **Operational Reports**, with its type, the time it was generated (UTC), its owner and its installation.
- The report page says **3 alerts of evidence** and lists the three alerts you picked, each with the same score band and percentage as on the Alerts page.
- A **JSON** button that downloads the same report as a file.

Result: ☐ Pass ☐ Fail   Notes: ______________________________________________

---

### Scenario 4 — An administrator approves a new installation

**Needs preparing first:** a new installation that is waiting for approval. In cycle 2 this is PC B's new installation (TC-17) or the new person's installation in Part B, whichever comes first. **You sign in as:** the Administrator account.

1. Click **Nodes**.
2. Under the pending requests, find the request. Check both the person and the installation ID they sent you, not just the computer name. Click **Approve**.
3. Ask the person to reload their **Dashboard**.

**You should see**
- Before approval, their Dashboard says **Awaiting approval** and monitoring is refused.
- After you click **Approve**, the membership shows **Approved**.
- Their Dashboard shows **Node approved** and they can start monitoring **without restarting** AlgoGuard.

If no installation was waiting, tick *Not run* and say so.

Result: ☐ Pass ☐ Fail ☐ Not run   Notes: ______________________________________________

---

### Scenario 5 — An administrator checks every computer

**Needs preparing first:** PC A and PC B have both run a sample replay with *Save attacks* in this cycle. **You sign in as:** the Administrator account.

1. Click **Computers**.
2. Click **Alerts**, choose **All computers you can see** in the **Computer** filter, and look at the newest alerts.
3. Do the same on **Logs** and **Reports**.

**You should see**
- **Computers** lists PC A and PC B, each with its AlgoGuard version (`0.6.0+…`), its model, its capture state and its records waiting, not stored or rejected. A computer nobody is signed in to shows **Stale** after 3 minutes.
- **Alerts**, **Logs** and **Reports** show records from both computers, each labelled with its computer.
- Alerts from PC B are **View only** on PC A: there is no Acknowledge or Resolve button on them.

Result: ☐ Pass ☐ Fail   Notes: ______________________________________________

---

## Part B — A person new to AlgoGuard follows the guide (TC-18)

**Who:** someone who has never installed or used AlgoGuard. **Where:** a Windows 10 or 11 PC with an internet connection. It can be PC A in a new folder, PC B in a new folder, or their own PC. **They get:** the analyst package (`AlgoGuard-analyst-0.6.0.zip`), the project URL and publishable key, and a new analyst account made on the **Accounts** page. They get no other help.

**The observer** sits beside them, gives no help unless they are completely stuck, and writes down:
- the time they start and the time AlgoGuard first shows a verdict;
- every place they hesitated, asked a question or did something the guide did not intend, with the guide section;
- any message they did not understand.

**They do, using only `docs/ANALYST_GUIDE.md` (inside the package):**
1. Install AlgoGuard (guide section 2) and start it (section 3).
2. Sign in and get the installation approved (section 4). The Administrator approves it (this can be Part A's scenario 4).
3. Run a sample replay with *Save attacks* for a minute (section 5).
4. Open **Alerts**, pick one alert and say in their own words what its attack score means (section 7).
5. Close AlgoGuard the way the guide says.

**Pass when:** steps 1–5 are done without the observer's help, without training and without any secret key; and their explanation in step 4 does not treat the score as certain or as a measure of harm. Each point of confusion becomes a Low DEF card against the guide, unless it blocked them (then Medium).

Result: ☐ Pass ☐ Fail ☐ Not run   Time to first verdict: ________   Notes: ______________________________________________

---

## Results and decision

The Test Log (C2-LOG-01) records each scenario's result with its build, PC and time. Defects found are DEF cards from DEF-20 on the Testing Kanban, prioritised by the Product Owner. No signature is collected for cycle 2; the Lead Architect records the acceptance decision in the Test Completion Report (C2-DOC-06).
