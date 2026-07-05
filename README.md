# 🛡️ Guardian — DNS Parental Control System

Control Netflix access for students at a boarding school from anywhere in the
world. Uses **DNS-over-HTTPS (DoH)** — the same technology as AdGuard — so
rules work on WiFi **and** mobile data.

---

## How it works

```
Student's Phone / Laptop
        │
        │  All DNS queries (HTTPS)
        ▼
 Your Cloud Server  ←─── Your Dashboard (manage rules)
        │
        │  Block or allow based on:
        │  • Allowed days of the week
        │  • Allowed time window (e.g. 7 PM – 10 PM)
        │  • Daily watch-time limit
        ▼
    Netflix (or NXDOMAIN if blocked)
```

Each student gets a **unique token** embedded in their DNS URL.
Rules are enforced even on mobile data.

---

## Quick start (local testing)

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Run
python app.py

# 3. Open dashboard
#    http://localhost:5000
```

---

## Deploy to the cloud (free)

### Option A — Railway.app (recommended)

1. Push this folder to a GitHub repository
2. Go to https://railway.app → New Project → Deploy from GitHub
3. Select the repo — Railway auto-detects Python
4. Add a start command in railway.json:

```json
{ "build": { "builder": "NIXPACKS" },
  "deploy": { "startCommand": "gunicorn app:app --bind 0.0.0.0:$PORT" } }
```

5. Railway gives you a public HTTPS URL like:
   `https://guardian-production.up.railway.app`

### Option B — Render.com

1. New → Web Service → connect repo
2. Build command : `pip install -r requirements.txt`
3. Start command : `gunicorn app:app --bind 0.0.0.0:$PORT`
4. Free tier is sufficient

### Environment variables (set in the dashboard)

| Variable     | Description                    | Default       |
|-------------|--------------------------------|---------------|
| `SECRET_KEY` | Flask session secret           | auto-generated|
| `PORT`       | Port to listen on              | 5000          |

---

## Install on student devices

### 🍎 iPhone / iPad (iOS)

1. Open the student's profile page in your dashboard
2. Click **iOS Profile** → a `.mobileconfig` file downloads
3. Send it to the student (email, AirDrop, iCloud Drive)
4. Student taps the file
5. Go to: **Settings → General → VPN & Device Management → Guardian → Install**
6. Done — applies to WiFi and mobile data instantly

### 🪟 Windows Laptop

1. Open the student's profile page
2. Click **Windows .ps1** → script downloads
3. Send to student
4. Student right-clicks the file → **Run with PowerShell**
5. If prompted, choose "Run as Administrator"
6. Done

### 🤖 Android Phone

**Method 1 — Chrome browser (easiest)**
1. Open student profile → click **Android Guide**
2. Copy the DoH URL shown
3. Chrome → `⋮` → Settings → Privacy and security → Security
4. Use secure DNS → With (Custom) → paste URL
5. Done (applies to all Chrome traffic including Netflix app links)

**Method 2 — System DNS (Android 9+)**
1. Settings → Network & internet → Advanced → Private DNS
2. Select "Private DNS provider hostname"
3. Enter your server hostname (without `https://`)
4. Note: system DoT does not include the student token;
   use browser method for per-student rules.

---

## Dashboard features

| Feature | Description |
|---|---|
| **Student cards** | Live status ring — green (active) / red (blocked) / amber (limit) |
| **Per-student rules** | Days, time window, daily watch-time cap |
| **Emergency block** | Block/unblock a single student instantly |
| **Block All / Allow All** | Top-right nav buttons — one click for everyone |
| **Profile download** | iOS `.mobileconfig` and Windows `.ps1` generated per student |
| **Activity logs** | Every DNS query logged with timestamp and reason |
| **Watch-time tracking** | Session-based — resets daily at midnight |

---

## Architecture

```
guardian/
├── app.py            # Flask: routes, API, profile generators
├── database.py       # SQLite: students, sessions, logs
├── rule_engine.py    # Decision logic: day / time / limit checks
├── dns_resolver.py   # DoH handler: parse → check → upstream
├── requirements.txt
└── templates/
    ├── base.html     # Nav, shared styles, toast
    ├── index.html    # Student grid overview
    ├── student.html  # Per-student detail & profiles
    └── logs.html     # Filterable activity log
```

---

## Netflix domains blocked

The rule engine intercepts these domains (and all their subdomains):

```
netflix.com     nflxvideo.net   nflximg.net
nflxext.com     nflxso.net      netflixdnstest.com
fast.com        netflix.net     (+ all subdomains)
```

---

## Adding more sites to block

Edit `NETFLIX_DOMAINS` in `rule_engine.py` to add any domain:

```python
NETFLIX_DOMAINS = {
    'netflix.com', 'nflxvideo.net',   # existing
    'youtube.com', 'www.youtube.com', # add YouTube
    'tiktok.com',  'www.tiktok.com',  # add TikTok
}
```

Restart the server — rules apply immediately.

---

## Security notes

- Student tokens are 20-character cryptographically random strings
- Fail-safe: if server is unreachable, DoH clients fall back silently
  (no internet = Netflix also unreachable — natural block)
- The iOS profile sets `PayloadRemovalDisallowed: false` by default;
  change to `true` for stricter enforcement (requires supervision)
- Windows script locks DoH policy via registry Group Policy key

---

## License
MIT — build freely, use responsibly.
