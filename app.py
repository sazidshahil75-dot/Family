"""Family Circle - consent-based live location sharing.

Rules enforced on the server (not just in the UI):
  * A person only becomes visible after they open their invite link and agree.
  * Location is accepted and shown ONLY while their status is "active".
  * Pause / stop / expiry immediately erases the stored location.
  * Only the latest point is stored. There is no history.
  * "Delete my data" removes the person entirely.
"""
import hmac
import os
import secrets
import time
from datetime import datetime, timedelta, timezone
from functools import wraps

from flask import (Flask, abort, jsonify, redirect, render_template, request,
                   session, url_for)
from flask_sqlalchemy import SQLAlchemy
from werkzeug.middleware.proxy_fix import ProxyFix

app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)

db_url = os.environ.get("DATABASE_URL", "sqlite:///familycircle.db")
if db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql://", 1)

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
GMAPS_KEY = os.environ.get("GOOGLE_MAPS_API_KEY", "").strip()
INVITE_DAYS = 7


def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def iso(dt):
    return dt.isoformat() + "Z" if dt else None


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
    updated_at = db.Column(db.DateTime)

    def clear_location(self):
        self.lat = self.lng = self.accuracy = self.battery = self.updated_at = None

    def refresh(self):
        """Turn an expired share into a stopped one and erase the location."""
        if self.status == "active" and self.expires_at and self.expires_at <= utcnow():
            self.status = "stopped"
            self.expires_at = None
            self.clear_location()
            db.session.commit()


with app.app_context():
    try:
        db.create_all()
    except Exception as exc:  # another worker may have created the tables
        app.logger.warning("create_all: %s", exc)


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


def owner_view(p):
    p.refresh()
    d = {"id": p.id, "name": p.name, "status": p.status, "expires_at": iso(p.expires_at)}
    if p.status == "invited":
        if p.created_at < utcnow() - timedelta(days=INVITE_DAYS):
            d["status"] = "expired_invite"
        else:
            d["invite_url"] = url_for("join", token=p.invite_token, _external=True)
    if p.status == "active" and p.lat is not None:
        d.update(lat=p.lat, lng=p.lng, accuracy=p.accuracy, battery=p.battery,
                 updated_at=iso(p.updated_at))
    return d


def share_state(p):
    return {"name": p.name, "status": p.status, "expires_at": iso(p.expires_at),
            "updated_at": iso(p.updated_at)}


# ---------- owner pages ----------
@app.route("/")
@owner_required()
def dashboard():
    return render_template("dashboard.html", owner=OWNER_NAME, gmaps_key=GMAPS_KEY)


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


@app.get("/api/people")
@owner_required(api=True)
def api_people():
    people = Person.query.order_by(Person.created_at).all()
    return jsonify([owner_view(p) for p in people])


@app.post("/api/invite")
@owner_required(api=True)
def api_invite():
    name = (request.get_json(silent=True) or {}).get("name", "").strip()[:60]
    if not name:
        return jsonify(error="Enter a name"), 400
    p = Person(name=name, invite_token=secrets.token_urlsafe(24))
    db.session.add(p)
    db.session.commit()
    return jsonify(owner_view(p))


@app.post("/api/people/<int:pid>/remove")
@owner_required(api=True)
def api_remove(pid):
    p = db.session.get(Person, pid) or abort(404)
    db.session.delete(p)
    db.session.commit()
    return jsonify(ok=True)


# ---------- invitee pages ----------
@app.route("/join/<token>", methods=["GET", "POST"])
def join(token):
    p = Person.query.filter_by(invite_token=token, status="invited").first()
    if not p or p.created_at < utcnow() - timedelta(days=INVITE_DAYS):
        return render_template("message.html", title="This invite link is no longer valid",
                               text=f"Ask {OWNER_NAME} for a new one."), 404
    if request.method == "POST":
        if request.form.get("agree") != "yes":
            abort(400)
        delta = parse_hours(request.form.get("hours"))
        p.share_token = secrets.token_urlsafe(32)
        p.invite_token = None  # the invite link works once
        p.status = "active"
        p.consented_at = utcnow()
        p.expires_at = utcnow() + delta if delta else None
        db.session.commit()
        return redirect(url_for("share", token=p.share_token))
    return render_template("consent.html", p=p, owner=OWNER_NAME)


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
    p.lat, p.lng = lat, lng
    p.accuracy = float(data["accuracy"]) if data.get("accuracy") is not None else None
    b = data.get("battery")
    p.battery = int(b) if isinstance(b, (int, float)) and 0 <= b <= 100 else None
    p.updated_at = utcnow()
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
    resp.headers["Referrer-Policy"] = "no-referrer"
    resp.headers["Permissions-Policy"] = "geolocation=(self)"
    return resp


if __name__ == "__main__":
    app.run(debug=True, port=int(os.environ.get("PORT", 5000)))
