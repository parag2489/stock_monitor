# Deploying to Render (Free Tier)

Two ways to deploy: the Blueprint (`render.yaml`) does it in one click; manual
setup takes a few extra clicks but shows you every option along the way.
Either way ends with a live URL like `https://market-screener.onrender.com`.

## What you'll need

- A GitHub account with this project pushed to a repo (see below)
- A Render account (free, sign up with GitHub — no credit card required)

## 1. Push this project to GitHub

If you haven't already:

```bash
cd path/to/screener
git init -b main
git add .
git commit -m "Prepare for Render deployment"
git remote add origin git@github.com:YOUR_USERNAME/screener.git
git push -u origin main
```

Public or private repo both work fine with Render's free tier.

## 2. Deploy — Option A: Blueprint (one click)

1. Go to <https://dashboard.render.com/>
2. **New +** → **Blueprint**
3. Connect the repo you just pushed
4. Render detects `render.yaml` and shows you the `market-screener` service
   with the Free plan pre-selected
5. **Apply** — first deploy takes 2–3 minutes

That's it. `render.yaml` already specifies the free plan, the build command,
and the correct start command (`--host 0.0.0.0 --port $PORT`, which Render
requires — binding to `localhost` will fail health checks).

## 2. Deploy — Option B: Manual (if you skip the Blueprint)

1. **New +** → **Web Service**
2. Connect the repo
3. Fill in:
   - **Name:** `market-screener` (or anything you like — this becomes part of your URL)
   - **Runtime:** Python 3
   - **Build Command:** `pip install -r requirements.txt`
   - **Start Command:** `uvicorn server:app --host 0.0.0.0 --port $PORT`
   - **Instance Type:** Free
4. **Create Web Service**

## 3. Verify it's live

Once the deploy finishes, Render gives you a URL like:

```
https://market-screener.onrender.com
```

Open it — you should see the Market Screener with live TradingView data,
exactly like `localhost:8000`. Try `/trends` too for the Fear & Greed page.

## 4. (Optional) Point your own domain at it

If you bought `marketmovement.com` or similar:

1. In the Render dashboard: your service → **Settings** → **Custom Domains** → **Add Custom Domain**
2. Render gives you a CNAME (or A record) target
3. Add that record at your domain registrar's DNS settings
4. Render auto-provisions a free HTTPS certificate once DNS propagates (can take up to a few hours)

## What to expect on the free tier

- **Cold starts.** After 15 minutes with no traffic, the service spins down.
  The next visit takes 30–60 seconds to wake back up before the page loads.
  Totally fine for personal, occasional use — just don't be alarmed by the
  first load feeling slow.
- **750 free instance-hours/month**, shared across any free services in your
  Render account. One service running occasionally (with cold-start gaps)
  will never come close to this.
- **No changes needed to app behavior.** Everything — the screener, sectors
  tab, sector drill-down, Fear & Greed gauge — works identically to your
  local `localhost:8000` version.

## Redeploying after changes

Render auto-deploys on every push to `main` by default. Once this is set up,
your workflow becomes:

```bash
git add .
git commit -m "Describe your change"
git push
```

Render picks it up and redeploys automatically — no manual redeploy step
needed. You can watch progress live in the **Events** tab of your service.

## Troubleshooting

**Deploy fails at build step.** Check the **Logs** tab — almost always a
missing package in `requirements.txt`.

**Deploy succeeds but the page 502s.** Usually means the start command isn't
binding to `0.0.0.0:$PORT`. Double check your **Settings → Start Command**
matches exactly: `uvicorn server:app --host 0.0.0.0 --port $PORT`.

**Fear & Greed or Screener data doesn't load once live.** Open the browser
console on the deployed site. If you see CORS or network errors, check
Render's **Logs** tab for the actual upstream error — TradingView or CNN
occasionally rate-limit server IPs, which looks different from the sandboxed
errors you'd see locally.
