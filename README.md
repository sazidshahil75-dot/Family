# Family Circle

Consent-based live location sharing. Flask + Postgres, with a live dark map.

## How it works
1. You sign in with `OWNER_PASSWORD` and create an invite link. One link can be sent to many people, even ones with the same name; each person who opens it chooses their own name and agrees for themselves.
2. They open the link, read exactly what is shared, choose a duration and tick "I agree".
3. Their page shows a **SHARING LIVE** banner and Pause / Stop / Delete buttons.
4. Pause, stop or expiry erases their location and speed on the server immediately, so you cannot see them.
5. Only the latest point and current speed are stored. There is no history. Delete removes the person completely.

## Deploy on Railway
1. Push this folder to a GitHub repo.
2. On railway.com: **New Project > Deploy from GitHub repo** and pick it. Railway reads `railway.json` and `requirements.txt` automatically.
3. In the project, click **+ New > Database > Add PostgreSQL**.
4. Open your app service > **Variables** and add:
   - `DATABASE_URL` = `${{Postgres.DATABASE_URL}}` (reference the Postgres service)
   - `OWNER_PASSWORD` = a long password
   - `SECRET_KEY` = a long random string (keeps you signed in across restarts)
   - `OWNER_NAME` = Sahil
5. **Settings > Networking > Generate Domain**. Open it, sign in, and create your first invite.
HTTPS comes with the Railway domain, and phones need it for location.

Tables are created automatically on first start. The health check is `/healthz`.

## Map and speed
The map uses OpenStreetMap tiles (Dark is the same tiles with a dark filter) and Esri satellite imagery. No API key is needed. CARTO tiles were dropped because CARTO now stamps "API KEY REQUIRED" on keyless tiles.
Speed (km/h) and direction come from the phone's GPS. If a phone does not report speed, the server works it out from the last two points.
Existing databases are upgraded automatically on start (new columns and the invites table).

## Run locally
    pip install -r requirements.txt
    OWNER_PASSWORD=test python app.py
Phones only allow location on HTTPS, so test sharing on the deployed URL (or localhost on the same device).

## Limits to know
- Web pages only get location while open. Android Chrome can last a little longer; iPhones stop when the screen locks.
  The share page asks for a screen wake lock to help. For always-on sharing you need a native app (Flutter / React Native).
- The map refreshes every 5 seconds by polling.
