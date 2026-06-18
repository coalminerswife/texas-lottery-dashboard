# Deploying the dashboard (Streamlit Community Cloud)

Goal: a URL you can open on any device (home laptop, phone) with no install, kept
current by a daily GitHub Action. Free.

The repo is already initialized and committed locally. You only need to (1) put it on
GitHub and (2) connect Streamlit Cloud. Steps 1 needs your GitHub login.

## 1. Put the code on GitHub

### Easiest: GitHub CLI
```bash
# install the GitHub CLI (Homebrew)
brew install gh

# log in (opens a browser; follow the prompts)
gh auth login

# from the project folder, create the repo and push in one shot
cd ~/texas-lottery-dashboard
gh repo create texas-lottery-dashboard --public --source=. --remote=origin --push
```

### Alternative: create the repo on github.com
1. Go to https://github.com/new, name it `texas-lottery-dashboard`, Public, **don't**
   add a README (we already have commits). Create.
2. Then:
   ```bash
   cd ~/texas-lottery-dashboard
   git remote add origin https://github.com/coalminerswife/texas-lottery-dashboard.git
   git branch -M main
   git push -u origin main
   ```
   (It will prompt for your GitHub username + a Personal Access Token as the password.)

## 2. Deploy on Streamlit Community Cloud
1. Go to https://share.streamlit.io and sign in with GitHub (authorize it).
2. Click **New app** → **Deploy a public app from GitHub**.
3. Repository: `coalminerswife/texas-lottery-dashboard` · Branch: `main` · Main file: `app.py`.
4. Click **Deploy**. First build takes a couple minutes; then you get a URL like
   `https://<something>.streamlit.app` — open it anywhere.

## 3. Turn on the daily refresh
- The daily scraper lives in `.github/workflows/scrape.yml` and runs at ~9am Central.
- After the first push, open the repo's **Actions** tab. If prompted, enable workflows.
- Click **Daily scrape** → **Run workflow** once to seed a fresh commit and confirm it works.
- Each daily run commits new data; Streamlit Cloud auto-redeploys, so the URL stays current.

## Notes
- The committed `data/lottery.db` seeds the history, so the hosted app has data on day one.
- Your local Mac `launchd` scraper can stay or be removed; the cloud Action is now the
  source of truth for the hosted version. Running both is harmless.
- The repo is public (lottery data, no secrets). To make it private, Streamlit Community
  Cloud still works — just pick the private repo when deploying.
