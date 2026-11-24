# job-agent

Daily job sourcing for Faraz's tracker. Runs free on GitHub Actions every
morning (about 05:45 IST) and works together with the Claude scheduled tasks.

## How the two halves split the work

| Time (IST) | Who | What |
|---|---|---|
| ~05:45 | **This code** (GitHub Actions) | Searches LinkedIn, Indeed, Naukri, Glassdoor (+ Bayt on Sundays) and every company job board already in your tracker (Workday, Greenhouse, Lever, Ashby, SmartRecruiters). Removes duplicates, filters out agencies/off-target titles, keyword-scores, checks the link is open, writes new candidates to the **Job Inbox** tab. Re-checks old tracker links and writes **Y (Active/Expired)** and **Z (date)**. Logs the run in **Pipeline Log**. |
| 07:40 | Claude | Reads the Job Inbox, scores each job against your resume, adds 55+ jobs to the Tracker (green "Yes" column), builds a tailored resume + cover letter PDF. |
| 08:45 | Claude | Gmail status sync, hiring-manager outreach **drafts** (never sent). |
| every 6 h | Claude | Gmail status sync. |
| 07:10 / 19:10 | Claude | Interview prep packs for rows at Recruiter Screen / Interview. |
| Sun 10:40 | Claude | Weekly pipeline review. |

The code never applies to jobs and never logs into any site.

## How new companies are found (the "Boards" tab)
The list of company job boards grows by itself:
1. **From your tracker**: every Workday / Greenhouse / Lever / Ashby / SmartRecruiters / Workable link already there.
2. **From job-site results**: when LinkedIn, Indeed, Naukri, Glassdoor or Google Jobs shows a procurement job whose apply link goes to a company's own board, that company is added — even if you have never heard of it.
3. **From config.yaml**: starting boards you add by hand.

Each run searches up to 80 boards, never-searched first, then the ones searched longest ago, so a list of hundreds rotates through every few days. A board that errors 3 runs in a row is switched off (Active = No); set it back to Yes to retry.

Links from company boards are the employer's own posting page (e.g. `company.wd3.myworkdayjobs.com/...`), confirmed open through that company's own system.

## One-time setup (about 15 minutes)

### 1. Google service account (lets the code edit your tracker)
1. Go to https://console.cloud.google.com → top bar → **New project** → name it `job-agent` → Create.
2. Search **Google Sheets API** → **Enable**.
3. Menu → **IAM & Admin → Service Accounts → Create service account** → name `job-agent` → Create and continue → skip roles → Done.
4. Click the new account → **Keys → Add key → Create new key → JSON**. A `.json` file downloads. Keep it private.
5. Copy the account's email (looks like `job-agent@job-agent-123456.iam.gserviceaccount.com`).
6. Open your tracker sheet → **Share** → paste that email → **Editor** → untick "Notify" → Share.

### 2. GitHub repository
1. Create a **private** repo named `job-agent` on github.com (no README).
2. Upload all files from this folder (keep the `.github/workflows` folder).
   *Or tell Claude the repo exists and Claude will push the code for you.*

### 3. Add the secret
Repo → **Settings → Secrets and variables → Actions → New repository secret**
- Name: `GOOGLE_SERVICE_ACCOUNT_JSON`
- Value: open the downloaded `.json` file, copy **everything**, paste.

### 4. First run
Repo → **Actions → Daily job search → Run workflow** → set `dry_run` to `true` → Run.
Open the run and check the last step's output (it lists what it found). If it looks right, run again with `dry_run` = `false`. After that it runs by itself every morning.

## Changing things
Everything is in `config.yaml`: search terms per weekday, cities, which sites, title keywords, excluded words, score threshold, how many links to re-check. Add extra company boards under `ats:`.

## Costs
- GitHub Actions: free (private repos get 2,000 minutes/month; one run is ~15–30 minutes).
- JobSpy and the company-board APIs are free. No Firecrawl, Tavily or Apify credits are used by this code.

## Known limits
- LinkedIn sometimes rate-limits GitHub's servers; that site may return fewer results some days. The run continues with the other sites and records the error in Pipeline Log.
- LinkedIn job links can't be checked automatically, so they stay "Unknown" in column Y.
- Glassdoor blocks scrapers often; expect occasional zero results from it.

## Run locally (optional)
```bash
pip install -r requirements.txt
export GOOGLE_SERVICE_ACCOUNT_FILE=/path/to/key.json
python -m job_agent.run --mode all --dry-run    # read only
python -m pytest -q                              # tests
```
