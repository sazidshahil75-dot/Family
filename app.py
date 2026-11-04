"""Family Circle - consent-based live location sharing.

Rules enforced on the server (not just in the UI):
  * A person only becomes visible after they open an invite link and agree.
    One invite link can be used by many people; each one agrees for themselves.
  * Location is accepted and shown ONLY while their status is "active".
  * Pause / stop / expiry immediately erases the stored location.
  * Only the latest point (and current speed) is stored. There is no history.
  * "Delete my data" removes the person entirely.
"""
import hmac
import math
import os
import secrets
import time
from datetime import datetime, timedelta, timezone
from functools import wraps

from flask import (Flask, abort, jsonify, redirect, render_template, request,
                   session, url_for)
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import func, inspect, text
from werkzeug.middleware.proxy_fix import ProxyFix

app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)

def resolve_db_url():
    raw = (os.environ.get("DATABASE_URL") or "").strip().strip('"').strip("'")
    if not raw:
        return "sqlite:///familycircle.db"
    for prefix in ("postgres://", "postgresql://"):
        if raw.startswith(prefix):
            raw = "postgresql+psycopg2://" + raw[len(prefix):]
            break
    if not raw.startswith(("postgresql://", "postgresql+psycopg2://", "sqlite:///")):
        print("WARNING: DATABASE_URL is not a valid database URL (got %r). "
              "In Railway, set it to ${{Postgres.DATABASE_URL}} using your Postgres service's exact name. "
              "Falling back to a temporary SQLite file; data will be lost on redeploy." % raw[:40], flush=True)
        return "sqlite:///familycircle.db"
    return raw


db_url = resolve_db_url()

app.config.update(
    SQLALCHEMY_DATABASE_URI=db_url,
    SECRET_KEY=os.environ.get("SECRET_KEY") or secrets.token_hex(32),
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=bool(os.environ.get("RAILWAY_ENVIRONMENT")),
    PERMANENT_SESSION_LIFETIME=timedelta(days=30),
)
db = SQLAlchemy(app)

OWNER_NAME = os.environ.get("OWNER_NAME", "Sahil")
OWNER_PASSWORD = os.environ.get("OWNER_PASSWORD", "")
INVITE_DAYS = 14


def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def iso(dt):
    return dt.isoformat() + "Z" if dt else None


class Invite(db.Model):
    """A reusable invite link. Anyone who opens it can join (after consenting)."""
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(60), nullable=False)
    token = db.Column(db.String(64), unique=True, nullable=False)
    created_at = db.Column(db.DateTime, default=utcnow)


class Person(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(60), nullable=False)
    invite_token = db.Column(db.String(64), unique=True)
    share_token = db.Column(db.String(64), unique=True)
    status = db.Column(db.String(10), default="invited")  # invited|active|paused|stopped
    created_at = db.Column(db.DateTime, default=utcnow)
    consented_at = db.Column(db.DateTime)
    expires_at = db.Column(db.DateTime)
    lat = db.Column(db.Float)
    lng = db.Column(db.Float)
    accuracy = db.Column(db.Float)
    battery = db.Column(db.Integer)
    speed = db.Column(db.Float)      # metres per second
    heading = db.Column(db.Float)    # degrees, 0 = north
    invite_id = db.Column(db.Integer)
    updated_at = db.Column(db.DateTime)

    def clear_location(self):
        self.lat = self.lng = self.accuracy = self.battery = self.updated_at = None
        self.speed = self.heading = None

    def refresh(self):
        """Turn an expired share into a stopped one and erase the location."""
        if self.status == "active" and self.expires_at and self.expires_at <= utcnow():
            self.status = "stopped"
            self.expires_at = None
            self.clear_location()
            db.session.commit()


def ensure_columns():
    """create_all() never alters existing tables, so add the new columns by hand."""
    try:
        have = {c["name"] for c in inspect(db.engine).get_columns("person")}
        for col, ddl in (("speed", "FLOAT"), ("heading", "FLOAT"), ("invite_id", "INTEGER")):
            if col not in have:
                db.session.execute(text(f"ALTER TABLE person ADD COLUMN {col} {ddl}"))
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        app.logger.warning("ensure_columns: %s", exc)


with app.app_context():
    try:
        db.create_all()
    except Exception as exc:  # another worker may have created the tables
        app.logger.warning("create_all: %s", exc)
    ensure_columns()


# ---------- helpers ----------
def owner_required(api=False):
    def deco(fn):
        @wraps(fn)
        def wrapper(*a, **kw):
            if not session.get("owner"):
                return (jsonify(error="login required"), 401) if api else redirect(url_for("login"))
            if api and request.method == "POST" and request.headers.get("X-Requested-With") != "fetch":
                abort(400)
            return fn(*a, **kw)
        return wrapper
    return deco


def person_by_share(token):
    p = Person.query.filter_by(share_token=token).first()
    if not p:
        abort(404)
    p.refresh()
    return p


def parse_hours(value):
    try:
        h = float(value)
    except (TypeError, ValueError):
        return None
    return timedelta(hours=h) if 0 < h <= 24 * 14 else None


def parse_float(value, lo, hi):
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return v if lo <= v <= hi else None  # NaN fails this test too


def haversine(lat1, lng1, lat2, lng2):
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lng2 - lng1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


def invite_expired(created_at):
    return created_at < utcnow() - timedelta(days=INVITE_DAYS)


def invite_view(i, counts):
    return {"id": i.id, "name": i.name, "joined": counts.get(i.id, 0),
            "url": url_for("join", token=i.token, _external=True),
            "expires_at": iso(i.created_at + timedelta(days=INVITE_DAYS)),
            "expired": invite_expired(i.created_at)}


def owner_view(p):
    p.refresh()
    d = {"id": p.id, "name": p.name, "status": p.status, "expires_at": iso(p.expires_at),
         "invite_id": p.invite_id}
    if p.status == "invited":
        if invite_expired(p.created_at):
            d["status"] = "expired_invite"
        else:
            d["invite_url"] = url_for("join", token=p.invite_token, _external=True)
    if p.status == "active" and p.lat is not None:
        d.update(lat=p.lat, lng=p.lng, accuracy=p.accuracy, battery=p.battery,
                 updated_at=iso(p.updated_at), heading=p.heading,
                 speed_kmh=round(p.speed * 3.6, 1) if p.speed is not None else None)
    return d


def share_state(p):
    return {"name": p.name, "status": p.status, "expires_at": iso(p.expires_at),
            "updated_at": iso(p.updated_at)}


# ---------- owner pages ----------
@app.route("/")
@owner_required()
def dashboard():
    return render_template("dashboard.html", owner=OWNER_NAME)


@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if not OWNER_PASSWORD:
        error = "OWNER_PASSWORD is not set. Add it in your Railway variables."
    elif request.method == "POST":
        if hmac.compare_digest(request.form.get("password", ""), OWNER_PASSWORD):
            session.permanent = True
            session["owner"] = True
            return redirect(url_for("dashboard"))
        time.sleep(1)  # slows down password guessing
        error = "Wrong password."
    return render_template("login.html", owner=OWNER_NAME, error=error)


@app.post("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.get("/api/overview")
@owner_required(api=True)
def api_overview():
    people = Person.query.order_by(Person.created_at, Person.id).all()
    invites = Invite.query.order_by(Invite.created_at.desc()).all()
    counts = dict(db.session.query(Person.invite_id, func.count(Person.id))
                  .filter(Person.invite_id.isnot(None)).group_by(Person.invite_id).all())
    return jsonify(people=[owner_view(p) for p in people],
                   invites=[invite_view(i, counts) for i in invites])


@app.post("/api/invite")
@owner_required(api=True)
def api_invite():
    name = (request.get_json(silent=True) or {}).get("name", "").strip()[:60]
    if not name:
        return jsonify(error="Enter a name"), 400
    i = Invite(name=name, token=secrets.token_urlsafe(24))
    db.session.add(i)
    db.session.commit()
    return jsonify(invite_view(i, {}))


@app.post("/api/invites/<int:iid>/remove")
@owner_required(api=True)
def api_invite_remove(iid):
    """Revoking a link stops new people joining. People who already joined stay."""
    i = db.session.get(Invite, iid) or abort(404)
    db.session.delete(i)
    db.session.commit()
    return jsonify(ok=True)


@app.post("/api/people/<int:pid>/remove")
@owner_required(api=True)
def api_remove(pid):
    p = db.session.get(Person, pid) or abort(404)
    db.session.delete(p)
    db.session.commit()
    return jsonify(ok=True)


# ---------- invitee pages ----------
def find_invite(token):
    """Returns (reusable_invite, legacy_person). At most one is not None."""
    inv = Invite.query.filter_by(token=token).first()
    if inv and not invite_expired(inv.created_at):
        return inv, None
    legacy = Person.query.filter_by(invite_token=token, status="invited").first()
    if legacy and not invite_expired(legacy.created_at):
        return None, legacy
    return None, None


def start_sharing(p, hours):
    delta = parse_hours(hours)
    p.share_token = secrets.token_urlsafe(32)
    p.status = "active"
    p.consented_at = utcnow()
    p.expires_at = utcnow() + delta if delta else None


@app.route("/join/<token>", methods=["GET", "POST"])
def join(token):
    inv, legacy = find_invite(token)
    if not inv and not legacy:
        return render_template("message.html", title="This invite link is no longer valid",
                               text=f"Ask {OWNER_NAME} for a new one."), 404
    if request.method == "POST":
        if request.form.get("agree") != "yes":
            abort(400)
        if legacy:  # old single-use link
            start_sharing(legacy, request.form.get("hours"))
            legacy.invite_token = None
            db.session.commit()
            return redirect(url_for("share", token=legacy.share_token))
        name = (request.form.get("name") or "").strip()[:60] or inv.name
        p = Person(name=name, invite_id=inv.id)
        start_sharing(p, request.form.get("hours"))
        db.session.add(p)
        db.session.commit()
        joined = dict(session.get("joined", {}))
        joined[token] = p.share_token
        session.permanent = True
        session["joined"] = joined
        return redirect(url_for("share", token=p.share_token))
    if inv and not request.args.get("new"):
        # Same phone opening the same link again: go back to the existing page
        # instead of creating a duplicate person.
        known = session.get("joined", {}).get(token)
        if known and Person.query.filter_by(share_token=known).first():
            return redirect(url_for("share", token=known))
    name = inv.name if inv else legacy.name
    return render_template("consent.html", name=name, editable=bool(inv), owner=OWNER_NAME)


@app.get("/s/<token>")
def share(token):
    p = person_by_share(token)
    return render_template("share.html", p=p, owner=OWNER_NAME, token=token, state=share_state(p))


@app.get("/api/s/<token>/state")
def api_share_state(token):
    return jsonify(share_state(person_by_share(token)))


@app.post("/api/s/<token>/location")
def api_location(token):
    p = person_by_share(token)
    if p.status != "active":  # paused/stopped: refuse and store nothing
        return jsonify(share_state(p)), 409
    data = request.get_json(silent=True) or {}
    try:
        lat, lng = float(data["lat"]), float(data["lng"])
        assert -90 <= lat <= 90 and -180 <= lng <= 180
    except Exception:
        return jsonify(error="bad location"), 400
    now = utcnow()
    accuracy = parse_float(data.get("accuracy"), 0, 100000)
    # Speed: trust the phone's own reading; otherwise work it out from the last point.
    speed = parse_float(data.get("speed"), 0, 120)  # m/s, 120 m/s = 432 km/h
    heading = parse_float(data.get("heading"), 0, 360)
    if speed is None and p.lat is not None and p.updated_at:
        dt = (now - p.updated_at).total_seconds()
        if 1 <= dt <= 120:
            dist = haversine(p.lat, p.lng, lat, lng)
            speed = dist / dt if dist > max(accuracy or 0, 10) else 0.0  # ignore GPS jitter
    if speed is not None and speed < 0.5:
        speed = 0.0
    if not speed:
        heading = None
    p.lat, p.lng = lat, lng
    p.accuracy = accuracy
    p.speed, p.heading = speed, heading
    b = data.get("battery")
    p.battery = int(b) if isinstance(b, (int, float)) and 0 <= b <= 100 else None
    p.updated_at = now
    db.session.commit()
    return jsonify(share_state(p))


@app.post("/api/s/<token>/control")
def api_control(token):
    p = person_by_share(token)
    data = request.get_json(silent=True) or {}
    action = data.get("action")
    if action == "pause":
        p.status = "paused"
        p.clear_location()
    elif action == "stop":
        p.status = "stopped"
        p.expires_at = None
        p.clear_location()
    elif action == "resume":
        delta = parse_hours(data.get("hours"))
        p.status = "active"
        p.expires_at = utcnow() + delta if delta else None
    elif action == "delete":
        db.session.delete(p)
        db.session.commit()
        return jsonify(deleted=True)
    else:
        return jsonify(error="unknown action"), 400
    db.session.commit()
    return jsonify(share_state(p))


@app.get("/healthz")
def healthz():
    return "ok", 200


@app.after_request
def headers(resp):
    resp.headers["Cache-Control"] = "no-store"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    resp.headers["Permissions-Policy"] = "geolocation=(self)"
    return resp


if __name__ == "__main__":
    app.run(debug=True, port=int(os.environ.get("PORT", 5000)))
