# CS2 Inventory Price Tracker

A self-hosted **trader's cockpit** that tracks high-value CS2 items from your Steam inventory: quantities from the Steam Inventory API, real cash prices from CSFloat, a Streamlit dashboard, and optional push alerts via ntfy.sh when prices move more than 5%.

## Features

- **Dashboard**: Streamlit UI with dark theme, metric cards per item, and a data table.
- **Background worker**: Polls CSFloat every 30 minutes and sends ntfy.sh notifications when a watchlist item's price changes by more than the configured threshold (default 5%).
- **Docker Compose**: One stack for `web` (Streamlit on port 8501) and `worker` (alerter).

## Quick start (local)

1. **Clone or create the project** and go into it:
   ```bash
   cd cs2-inventory-tracker
   ```

2. **Create your env file** from the example:
   ```bash
   copy .env.example .env
   ```
   Edit `.env` and set:
   - `STEAM_ID` – Your Steam 64-bit ID (e.g. from [steamid.io](https://steamid.io)).
   - `WATCHLIST` – Comma-separated market hash names (e.g. `AWP | Dragon Lore (Field-Tested),AK-47 | Redline (Field-Tested)`).
   - `NTFY_TOPIC` – Optional; ntfy.sh topic for push alerts (create at [ntfy.sh](https://ntfy.sh)).
   - `CSFLOAT_API_KEY` – Optional; if you have a CSFloat API key.
   - `ALERT_THRESHOLD` – Optional; default `0.05` (5%).

3. **Run with Docker Compose**:
   ```bash
   docker compose up --build -d
   ```
   Open **http://localhost:8501** for the dashboard.

4. **Or run locally without Docker**:
   ```bash
   python -m venv .venv
   .venv\Scripts\activate
   pip install -r requirements.txt
   streamlit run app.py
   ```
   In another terminal: `python worker.py` (for alerts).

## Deploy on Portainer

1. On your server, clone the repo (or add it as a stack from Git).
2. Create `.env` in the project root with the same variables as above.
3. In Portainer: **Stacks** → **Add stack** → **Web editor** (or **Git repository**).
   - If using Git: set repo URL and path to `docker-compose.yml`, and add the build context.
   - Or paste the contents of `docker-compose.yml` and ensure the build context is the directory that contains the Dockerfile.
4. Deploy the stack. The web service will listen on port 8501; map it in Portainer or your reverse proxy.

## GitHub setup and pushing

1. **Create a new repo on GitHub** (e.g. `cs2-inventory-tracker`). Do **not** initialize with a README if you already have one locally.

2. **Initialize Git and push** (run from `cs2-inventory-tracker`):
   ```bash
   git init
   git add .
   git commit -m "Initial commit: CS2 inventory tracker with Streamlit and worker"
   git branch -M main
   git remote add origin https://github.com/YOUR_USERNAME/cs2-inventory-tracker.git
   git push -u origin main
   ```
   Replace `YOUR_USERNAME` with your GitHub username. Use a personal access token (PAT) as password if you have 2FA.

3. **Keep .env out of Git**  
   `.env` is in `.gitignore`; only `.env.example` is committed. On a new machine or in Portainer, copy `.env.example` to `.env` and fill in secrets.

## Project layout

```
cs2-inventory-tracker/
├── docker-compose.yml   # web (Streamlit) + worker
├── Dockerfile
├── requirements.txt
├── .env.example
├── .gitignore
├── app.py               # Streamlit dashboard
├── worker.py            # Background alerter (ntfy)
└── README.md
```

## Notes

- **Rate limiting**: The app waits 1.5 seconds between CSFloat API calls to reduce 429 errors. On 429, the current run stops and will retry on the next cycle.
- **Private inventory**: If your Steam profile or inventory is private, quantities will show as 0; CSFloat prices can still be shown.
- **ntfy.sh**: Alerts are sent only when `NTFY_TOPIC` is set and a price moves by more than `ALERT_THRESHOLD`.
