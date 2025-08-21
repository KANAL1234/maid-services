# Maid Services (Streamlit) — Template

A minimal UrbanClap-style booking app for domestic help, built with Streamlit and using your GitHub repo as a JSON datastore.

## Quick start

1. Install deps
   ```bash
   pip install -r requirements.txt
   ```

2. Copy `.streamlit/secrets.toml.template` to `.streamlit/secrets.toml` and fill values:
   - GitHub token with `repo` scope
   - Owner, repo, branch
   - (Optional) SMTP settings for booking emails

3. Run
   ```bash
   streamlit run app.py
   ```

> On first run, the app will auto-create `data/users.json`, `data/workers.json`, and `data/bookings.json` in your GitHub repo using the Contents API.

## Deploy on Streamlit Community Cloud
- Push this repo to GitHub and mark it as a **Template** (Repo settings → Template repository).
- Create a new app from this repo.
- Set the same secrets in **App settings → Secrets** (paste your `secrets.toml` contents).

## CI
The included GitHub Action runs on pull requests and pushes:
- installs dependencies
- flake8 lint
- a small syntax/import smoke test

## License
MIT
