# Family Circle

Consent-based live location sharing. Flask + Postgres, Google Maps (or free OpenStreetMap if no key is set).

## How it works
1. You sign in with `OWNER_PASSWORD` and create an invite link for each person.
2. They open the link, read exactly what is shared, choose a duration and tick "I agree".
3. Their page shows a **SHARING LIVE** banner and Pause / Stop / Delete buttons.
4. Pause, stop or expiry erases their location on the server immediately, so you cannot see them.
5. Only the latest point is stored. There is no history. Delete removes the person completely.

## Deploy on Railway
1. Push this folder to a GitHub repo.
2. On railway.com: **New Project > Deploy from GitHub repo** and pick it. Railway reads `railway.json` and `requirements.txt` automatically.
3. In the project, click **+ New > Database > Add PostgreSQL**.
4. Open your app service > **Variables** and add:
   - `DATABASE_URL` = `${{Postgres.DATABASE_URL}}` (reference the Postgres service)
   - `OWNER_PASSWORD` = a long password
   - `SECRET_KEY` = a long random string (keeps you signed in across restarts)
   - `OWNER_NAME` = Sahil
   - `GOOGLE_MAPS_API_KEY` = optional
5. **Settings > Networking > Generate Domain**. Open it, sign in, and create your first invite.
HTTPS comes with the Railway domain, and phones need it for location.

Tables are created automatically on first start. The health check is `/healthz`.

## Google Maps key (optional)
Enable "Maps JavaScript API", turn on billing, and restrict the key to your Railway domain (HTTP referrers).
Without a key the app uses OpenStreetMap automatically.

## Run locally
    pip install -r requirements.txt
    OWNER_PASSWORD=test python app.py
Phones only allow location on HTTPS, so test sharing on the deployed URL (or localhost on the same device).

## Limits to know
- Web pages only get location while open. Android Chrome can last a little longer; iPhones stop when the screen locks.
  The share page asks for a screen wake lock to help. For always-on sharing you need a native app (Flutter / React Native).
- The map refreshes every 5 seconds by polling.
