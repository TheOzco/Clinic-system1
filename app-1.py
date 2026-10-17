"""
Clinic Data Server — ثبت مشتری، رتبه رنگی، روند درمان، کارت اتوماسیون
اجرا:  python app.py            (سرور)
سینک:  python app.py sync https://your-online-server   (کشیدن و فرستادن تغییرات)
"""
import os, sys, json, uuid, sqlite3
from datetime import datetime, timezone
from flask import Flask, request, jsonify, g, send_from_directory

HERE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("DB_PATH", os.path.join(HERE, "clinic.db"))
TOKEN = os.environ.get("APP_TOKEN", "")  # اگر خالی باشد بدون رمز کار می‌کند (فقط برای تست!)
PORT = int(os.environ.get("PORT", "8000"))

# ستون‌های هر جدول (id, created_at, updated_at, deleted خودکار اضافه می‌شوند)
TABLES = {
    "categories":    ["name", "sort"],
    "services":      ["category_id", "name", "price", "sessions", "active"],
    "tiers":         ["name", "color", "discount"],
    "tier_discounts":["tier_id", "service_id", "discount"],
    "clients":       ["name", "phone", "tier_id", "card_uid", "notes"],
    "treatments":    ["client_id", "service_id", "total", "price_paid"],
    "sessions":      ["treatment_id", "done_at", "note"],
}

app = Flask(__name__)


def now():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(_):
    d = g.pop("db", None)
    if d:
        d.close()


def init_db():
    con = sqlite3.connect(DB_PATH)
    for t, cols in TABLES.items():
        con.execute(
            f"CREATE TABLE IF NOT EXISTS {t} (id TEXT PRIMARY KEY, "
            + ", ".join(f"{c} TEXT" for c in cols)
            + ", created_at TEXT, updated_at TEXT, deleted INTEGER DEFAULT 0)"
        )
    con.execute("CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT)")
    if not con.execute("SELECT 1 FROM meta WHERE k='seeded'").fetchone():
        seed(con)
        con.execute("INSERT INTO meta VALUES ('seeded','1')")
    con.commit()
    con.close()


def put(con, table, id_, **vals):
    ts = now()
    cols = TABLES[table]
    con.execute(
        f"INSERT OR IGNORE INTO {table} (id,{','.join(cols)},created_at,updated_at,deleted) "
        f"VALUES (?,{','.join('?' for _ in cols)},?,?,0)",
        [id_] + [vals.get(c) for c in cols] + [ts, ts],
    )


def seed(con):
    # شناسه‌های ثابت تا دو سرور هنگام سینک رکورد تکراری نسازند
    for i, n in enumerate(["لیزر", "ماساژ", "پوست", "مو"]):
        put(con, "categories", f"cat-{i}", name=n, sort=i)
    sample = [
        ("cat-0", "لیزر زیر بغل", 500000, 8), ("cat-0", "لیزر پا", 1500000, 8),
        ("cat-0", "لیزر صورت", 700000, 8), ("cat-0", "لیزر تمام بدن", 4000000, 8),
        ("cat-1", "ماساژ سوئدی", 800000, 1), ("cat-1", "ماساژ ریلکسی", 700000, 1),
        ("cat-2", "پاکسازی پوست", 600000, 1), ("cat-2", "میکرونیدلینگ", 1800000, 4),
        ("cat-3", "مزوتراپی مو", 2000000, 6), ("cat-3", "PRP مو", 2500000, 4),
    ]
    for i, (c, n, p, s) in enumerate(sample):
        put(con, "services", f"svc-{i}", category_id=c, name=n, price=p, sessions=s, active=1)
    tiers = [("سفید", "#ffffff", 0), ("سبز", "#2ecc71", 5), ("آبی", "#3498db", 10),
             ("بنفش", "#9b59b6", 15), ("طلایی", "#f1c40f", 20)]
    for i, (n, c, d) in enumerate(tiers):
        put(con, "tiers", f"tier-{i}", name=n, color=c, discount=d)


# ---------- احراز هویت ساده ----------
@app.before_request
def auth():
    if TOKEN and request.path.startswith("/api/") and request.headers.get("X-Token") != TOKEN:
        return jsonify(error="unauthorized"), 401


@app.route("/")
def index():
    return send_from_directory(HERE, "index.html")


# ---------- CRUD عمومی ----------
def rows(sql, args=()):
    return [dict(r) for r in db().execute(sql, args).fetchall()]


@app.get("/api/<table>")
def list_(table):
    if table not in TABLES:
        return jsonify(error="not found"), 404
    return jsonify(rows(f"SELECT * FROM {table} WHERE deleted=0 ORDER BY created_at"))


@app.post("/api/<table>")
def create(table):
    if table not in TABLES:
        return jsonify(error="not found"), 404
    data = request.get_json(force=True)
    if table == "clients" and data.get("card_uid"):
        if rows("SELECT id FROM clients WHERE card_uid=? AND deleted=0", (data["card_uid"],)):
            return jsonify(error="این کارت قبلاً ثبت شده"), 409
    if table == "sessions" and not data.get("done_at"):
        data["done_at"] = now()
    id_ = uuid.uuid4().hex
    put(db(), table, id_, **data)
    db().commit()
    return jsonify(rows(f"SELECT * FROM {table} WHERE id=?", (id_,))[0]), 201


@app.put("/api/<table>/<id_>")
def update(table, id_):
    if table not in TABLES:
        return jsonify(error="not found"), 404
    data = {k: v for k, v in request.get_json(force=True).items() if k in TABLES[table]}
    if table == "clients" and data.get("card_uid"):
        if rows("SELECT id FROM clients WHERE card_uid=? AND deleted=0 AND id!=?", (data["card_uid"], id_)):
            return jsonify(error="این کارت قبلاً ثبت شده"), 409
    if data:
        sets = ",".join(f"{k}=?" for k in data)
        db().execute(f"UPDATE {table} SET {sets}, updated_at=? WHERE id=?",
                     list(data.values()) + [now(), id_])
        db().commit()
    return jsonify(rows(f"SELECT * FROM {table} WHERE id=?", (id_,))[0])


@app.delete("/api/<table>/<id_>")
def delete(table, id_):
    if table not in TABLES:
        return jsonify(error="not found"), 404
    db().execute(f"UPDATE {table} SET deleted=1, updated_at=? WHERE id=?", (now(), id_))
    db().commit()
    return jsonify(ok=True)


# ---------- منطق کسب‌وکار ----------
def discount_for(tier_id, service_id):
    """تخفیف اختصاصی رتبه روی خدمت؛ اگر نبود تخفیف پیش‌فرض رتبه."""
    r = rows("SELECT discount FROM tier_discounts WHERE tier_id=? AND service_id=? AND deleted=0",
             (tier_id, service_id))
    if r and r[0]["discount"] not in (None, ""):
        return float(r[0]["discount"])
    t = rows("SELECT discount FROM tiers WHERE id=? AND deleted=0", (tier_id,))
    return float(t[0]["discount"] or 0) if t else 0.0


def client_detail(client_id):
    c = rows("SELECT * FROM clients WHERE id=? AND deleted=0", (client_id,))
    if not c:
        return None
    c = c[0]
    c["tier"] = (rows("SELECT * FROM tiers WHERE id=?", (c["tier_id"],)) or [None])[0]
    c["treatments"] = []
    for t in rows("SELECT * FROM treatments WHERE client_id=? AND deleted=0 ORDER BY created_at", (client_id,)):
        s = (rows("SELECT * FROM services WHERE id=?", (t["service_id"],)) or [{}])[0]
        ses = rows("SELECT * FROM sessions WHERE treatment_id=? AND deleted=0 ORDER BY done_at", (t["id"],))
        total = int(t["total"] or 0)
        t.update(service_name=s.get("name"), done=len(ses), remaining=max(total - len(ses), 0), sessions=ses)
        c["treatments"].append(t)
    return c


@app.get("/api/client/<client_id>")
def client_get(client_id):
    c = client_detail(client_id)
    return (jsonify(c), 200) if c else (jsonify(error="not found"), 404)


@app.get("/api/card/<uid>")
def by_card(uid):
    r = rows("SELECT id FROM clients WHERE card_uid=? AND deleted=0", (uid,))
    if not r:
        return jsonify(error="کارت ناشناس"), 404
    return jsonify(client_detail(r[0]["id"]))


@app.get("/api/price/<client_id>/<service_id>")
def price(client_id, service_id):
    c = rows("SELECT tier_id FROM clients WHERE id=?", (client_id,))
    s = rows("SELECT price FROM services WHERE id=?", (service_id,))
    if not c or not s:
        return jsonify(error="not found"), 404
    d = discount_for(c[0]["tier_id"], service_id)
    p = float(s[0]["price"] or 0)
    return jsonify(price=p, discount=d, final=round(p * (1 - d / 100)))


# ---------- سینک بین سرور آنلاین و لوکال (آخرین تغییر برنده) ----------
@app.get("/api/sync/changes")
def sync_changes():
    since = request.args.get("since", "")
    return jsonify({t: rows(f"SELECT * FROM {t} WHERE updated_at>?", (since,)) for t in TABLES})


@app.post("/api/sync/apply")
def sync_apply():
    payload = request.get_json(force=True)
    con = db()
    n = 0
    for t, items in payload.items():
        if t not in TABLES:
            continue
        cols = ["id"] + TABLES[t] + ["created_at", "updated_at", "deleted"]
        for r in items:
            cur = con.execute(f"SELECT updated_at FROM {t} WHERE id=?", (r["id"],)).fetchone()
            if cur and cur["updated_at"] >= r["updated_at"]:
                continue
            con.execute(f"INSERT OR REPLACE INTO {t} ({','.join(cols)}) VALUES ({','.join('?' for _ in cols)})",
                        [r.get(c) for c in cols])
            n += 1
    con.commit()
    return jsonify(applied=n)


def cli_sync(remote):
    import urllib.request
    hdr = {"Content-Type": "application/json", "X-Token": TOKEN}
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute("CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT)")
    last = con.execute("SELECT v FROM meta WHERE k='last_sync'").fetchone()
    since = last["v"] if last else ""
    started = now()
    # فرستادن تغییرات محلی
    local = {t: [dict(r) for r in con.execute(f"SELECT * FROM {t} WHERE updated_at>?", (since,))] for t in TABLES}
    req = urllib.request.Request(remote.rstrip("/") + "/api/sync/apply", json.dumps(local).encode(), hdr)
    print("pushed:", urllib.request.urlopen(req).read().decode())
    # گرفتن تغییرات ریموت
    req = urllib.request.Request(remote.rstrip("/") + f"/api/sync/changes?since={since}", headers=hdr)
    remote_data = json.loads(urllib.request.urlopen(req).read())
    n = 0
    for t, items in remote_data.items():
        cols = ["id"] + TABLES[t] + ["created_at", "updated_at", "deleted"]
        for r in items:
            cur = con.execute(f"SELECT updated_at FROM {t} WHERE id=?", (r["id"],)).fetchone()
            if cur and cur["updated_at"] >= r["updated_at"]:
                continue
            con.execute(f"INSERT OR REPLACE INTO {t} ({','.join(cols)}) VALUES ({','.join('?' for _ in cols)})",
                        [r.get(c) for c in cols])
            n += 1
    con.execute("INSERT OR REPLACE INTO meta VALUES ('last_sync',?)", (started,))
    con.commit()
    print("pulled:", n)


if __name__ == "__main__":
    init_db()
    if len(sys.argv) >= 3 and sys.argv[1] == "sync":
        cli_sync(sys.argv[2])
    else:
        app.run(host="0.0.0.0", port=PORT)
