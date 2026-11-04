import os, sqlite3
from functools import wraps
from datetime import date, datetime
from flask import Flask, render_template, request, redirect, url_for, session, flash, g
from werkzeug.security import generate_password_hash, check_password_hash

APP_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(APP_DIR, "boarding_house.db")
app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "change-this-secret-key-before-deployment")
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"

def db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db

@app.teardown_appcontext
def close_db(_=None):
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()

def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS users (
      id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL,
      email TEXT NOT NULL UNIQUE, password_hash TEXT NOT NULL,
      role TEXT NOT NULL DEFAULT 'tenant', created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS rooms (
      id INTEGER PRIMARY KEY AUTOINCREMENT, room_number TEXT NOT NULL UNIQUE,
      room_type TEXT NOT NULL DEFAULT 'Shared', monthly_rate REAL NOT NULL DEFAULT 0,
      capacity INTEGER NOT NULL DEFAULT 1, status TEXT NOT NULL DEFAULT 'Available',
      notes TEXT DEFAULT ''
    );
    CREATE TABLE IF NOT EXISTS tenants (
      id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER UNIQUE,
      full_name TEXT NOT NULL, phone TEXT DEFAULT '', email TEXT DEFAULT '',
      room_id INTEGER, move_in TEXT, move_out TEXT, status TEXT NOT NULL DEFAULT 'Active',
      emergency_contact TEXT DEFAULT '', notes TEXT DEFAULT '',
      FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE SET NULL,
      FOREIGN KEY(room_id) REFERENCES rooms(id) ON DELETE SET NULL
    );
    CREATE TABLE IF NOT EXISTS payments (
      id INTEGER PRIMARY KEY AUTOINCREMENT, tenant_id INTEGER NOT NULL,
      amount REAL NOT NULL, payment_date TEXT NOT NULL, period TEXT NOT NULL,
      method TEXT NOT NULL DEFAULT 'Cash', reference TEXT DEFAULT '',
      status TEXT NOT NULL DEFAULT 'Paid', notes TEXT DEFAULT '',
      FOREIGN KEY(tenant_id) REFERENCES tenants(id) ON DELETE CASCADE
    );
    CREATE TABLE IF NOT EXISTS maintenance (
      id INTEGER PRIMARY KEY AUTOINCREMENT, tenant_id INTEGER,
      room_id INTEGER, title TEXT NOT NULL, description TEXT DEFAULT '',
      priority TEXT NOT NULL DEFAULT 'Normal', status TEXT NOT NULL DEFAULT 'Open',
      created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, resolved_at TEXT DEFAULT '',
      FOREIGN KEY(tenant_id) REFERENCES tenants(id) ON DELETE SET NULL,
      FOREIGN KEY(room_id) REFERENCES rooms(id) ON DELETE SET NULL
    );
    """)
    # Keep the starter system at six rooms maximum; create only enough to reach six.
    room_count = conn.execute("SELECT COUNT(*) FROM rooms").fetchone()[0]
    room_number = 101
    while room_count < 6:
        candidate = str(room_number)
        exists = conn.execute("SELECT 1 FROM rooms WHERE room_number=?", (candidate,)).fetchone()
        if not exists:
            conn.execute("""INSERT INTO rooms(room_number, room_type, monthly_rate, capacity, status, notes)
                            VALUES(?,?,?,?,?,?)""",
                         (candidate, "Shared", 3500, 2, "Available", "Starter room"))
            room_count += 1
        room_number += 1

    admin = conn.execute("SELECT id FROM users WHERE email=?", ("admin@example.com",)).fetchone()
    if not admin:
        conn.execute("INSERT INTO users(name,email,password_hash,role) VALUES(?,?,?,?)",
                     ("System Administrator", "admin@example.com",
                      generate_password_hash("admin123"), "admin"))
    conn.commit()
    conn.close()

@app.before_request
def ensure_db():
    init_db()

@app.context_processor
def inject_globals():
    return {"today": date.today().isoformat(), "current_user": session.get("user")}

def login_required(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        if not session.get("user"):
            flash("Please sign in to continue.", "warning")
            return redirect(url_for("login"))
        return fn(*args, **kwargs)
    return wrapped

def admin_required(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        if not session.get("user"):
            return redirect(url_for("login"))
        if session["user"]["role"] != "admin":
            flash("Administrator access is required.", "danger")
            return redirect(url_for("dashboard"))
        return fn(*args, **kwargs)
    return wrapped

def current_tenant():
    return db().execute("SELECT * FROM tenants WHERE user_id=?", (session["user"]["id"],)).fetchone()

@app.route("/")
def index():
    return redirect(url_for("dashboard") if session.get("user") else url_for("login"))

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        phone = request.form.get("phone", "").strip()
        if not name or not email or len(password) < 8:
            flash("Enter your name and email, and use a password with at least 8 characters.", "danger")
            return render_template("register.html")
        conn = db()
        try:
            cur = conn.execute("INSERT INTO users(name,email,password_hash,role) VALUES(?,?,?,?)",
                (name, email, generate_password_hash(password), "tenant"))
            conn.execute("INSERT INTO tenants(user_id,full_name,email,phone,status) VALUES(?,?,?,?,?)",
                (cur.lastrowid, name, email, phone, "Pending"))
            conn.commit()
        except sqlite3.IntegrityError:
            flash("That email is already registered.", "danger")
            return render_template("register.html")
        flash("Account created. You can now sign in. Your tenant profile is pending admin review.", "success")
        return redirect(url_for("login"))
    return render_template("register.html")

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        user = db().execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
        if user and check_password_hash(user["password_hash"], password):
            session.clear()
            session["user"] = {"id": user["id"], "name": user["name"], "email": user["email"], "role": user["role"]}
            return redirect(url_for("dashboard"))
        flash("Incorrect email or password.", "danger")
    return render_template("login.html")

@app.route("/logout")
def logout():
    session.clear()
    flash("You have been signed out.", "success")
    return redirect(url_for("login"))

@app.route("/dashboard")
@login_required
def dashboard():
    conn = db()
    is_admin = session["user"]["role"] == "admin"
    if is_admin:
        stats = {
            "rooms": conn.execute("SELECT COUNT(*) FROM rooms").fetchone()[0],
            "available": conn.execute("SELECT COUNT(*) FROM rooms WHERE status='Available'").fetchone()[0],
            "tenants": conn.execute("SELECT COUNT(*) FROM tenants WHERE status='Active'").fetchone()[0],
            "due": conn.execute("SELECT COALESCE(SUM(amount),0) FROM payments WHERE status='Unpaid'").fetchone()[0],
            "maintenance": conn.execute("SELECT COUNT(*) FROM maintenance WHERE status!='Resolved'").fetchone()[0],
            "income": conn.execute("SELECT COALESCE(SUM(amount),0) FROM payments WHERE status='Paid' AND substr(payment_date,1,7)=?",
                                   (date.today().strftime("%Y-%m"),)).fetchone()[0]
        }
        recent = conn.execute("""SELECT p.*, t.full_name FROM payments p JOIN tenants t ON p.tenant_id=t.id
                                 ORDER BY p.payment_date DESC,p.id DESC LIMIT 6""").fetchall()
        return render_template("dashboard.html", stats=stats, recent=recent, is_admin=True)
    tenant = current_tenant()
    payments = []
    room = None
    requests = []
    if tenant:
        payments = conn.execute("SELECT * FROM payments WHERE tenant_id=? ORDER BY payment_date DESC LIMIT 5", (tenant["id"],)).fetchall()
        if tenant["room_id"]:
            room = conn.execute("SELECT * FROM rooms WHERE id=?", (tenant["room_id"],)).fetchone()
        requests = conn.execute("SELECT * FROM maintenance WHERE tenant_id=? ORDER BY created_at DESC LIMIT 5", (tenant["id"],)).fetchall()
    return render_template("dashboard.html", is_admin=False, tenant=tenant, payments=payments, room=room, requests=requests)

@app.route("/rooms")
@login_required
def rooms():
    conn = db()
    if session["user"]["role"] == "admin":
        rows = conn.execute("""SELECT r.*, (SELECT COUNT(*) FROM tenants t WHERE t.room_id=r.id AND t.status IN ('Active','Pending')) AS occupants
                               FROM rooms r ORDER BY r.room_number""").fetchall()
    else:
        rows = conn.execute("""SELECT r.*,
                               (SELECT COUNT(*) FROM tenants t WHERE t.room_id=r.id AND t.status IN ('Active','Pending')) AS occupants
                               FROM rooms r
                               WHERE (r.status='Available' AND
                                      (SELECT COUNT(*) FROM tenants t WHERE t.room_id=r.id AND t.status IN ('Active','Pending')) < r.capacity)
                                  OR r.id=(SELECT room_id FROM tenants WHERE user_id=?)
                               ORDER BY r.room_number""",
                            (session["user"]["id"],)).fetchall()
    tenant = current_tenant() if session["user"]["role"] != "admin" else None
    return render_template("rooms.html", rooms=rows, tenant=tenant)

@app.route("/rooms/<int:room_id>/choose", methods=["POST"])
@login_required
def choose_room(room_id):
    if session["user"]["role"] == "admin":
        flash("Use the tenant management page to assign a room to a tenant.", "warning")
        return redirect(url_for("rooms"))
    conn = db()
    tenant = current_tenant()
    if not tenant:
        flash("Your tenant profile was not found. Please contact the administrator.", "danger")
        return redirect(url_for("rooms"))
    if tenant["room_id"]:
        flash("You already have a room assigned. Contact the administrator if you need to change it.", "warning")
        return redirect(url_for("rooms"))
    room = conn.execute("SELECT * FROM rooms WHERE id=?", (room_id,)).fetchone()
    if not room or room["status"] != "Available":
        flash("That room is no longer available. Please choose another room.", "danger")
        return redirect(url_for("rooms"))
    occupants = conn.execute("SELECT COUNT(*) FROM tenants WHERE room_id=? AND status IN ('Active','Pending')", (room_id,)).fetchone()[0]
    if occupants >= room["capacity"]:
        flash("That room is already at capacity. Please choose another room.", "danger")
        return redirect(url_for("rooms"))
    conn.execute("UPDATE tenants SET room_id=?, move_in=?, status='Active' WHERE id=?",
                 (room_id, date.today().isoformat(), tenant["id"]))
    if occupants + 1 >= room["capacity"]:
        conn.execute("UPDATE rooms SET status='Occupied' WHERE id=?", (room_id,))
    conn.commit()
    flash(f"Room {room['room_number']} selected successfully. You can now make a demo rent payment.", "success")
    return redirect(url_for("payments"))

@app.route("/rooms/add", methods=["POST"])
@admin_required
def add_room():
    if db().execute("SELECT COUNT(*) FROM rooms").fetchone()[0] >= 6:
        flash("This system is limited to six rooms.", "warning")
        return redirect(url_for("rooms"))
    try:
        db().execute("INSERT INTO rooms(room_number,room_type,monthly_rate,capacity,status,notes) VALUES(?,?,?,?,?,?)",
            (request.form["room_number"].strip(), request.form["room_type"], float(request.form["monthly_rate"]),
             max(1, int(request.form["capacity"])), request.form["status"], request.form.get("notes","").strip()))
        db().commit()
        flash("Room added successfully.", "success")
    except (sqlite3.IntegrityError, ValueError, KeyError):
        flash("Could not add room. Check the room number and values; room numbers must be unique.", "danger")
    return redirect(url_for("rooms"))

@app.route("/rooms/<int:room_id>/remove", methods=["POST"])
@admin_required
def remove_room(room_id):
    conn = db()
    room = conn.execute("SELECT room_number FROM rooms WHERE id=?", (room_id,)).fetchone()
    if not room:
        flash("Room not found.", "danger")
        return redirect(url_for("rooms"))
    occupants = conn.execute(
        "SELECT COUNT(*) FROM tenants WHERE room_id=? AND status IN ('Active','Pending')",
        (room_id,)
    ).fetchone()[0]
    if occupants:
        flash("Cannot remove a room with active or pending tenants. Check out or reassign them first.", "warning")
        return redirect(url_for("rooms"))
    conn.execute("DELETE FROM rooms WHERE id=?", (room_id,))
    conn.commit()
    flash(f"Room {room['room_number']} removed.", "success")
    return redirect(url_for("rooms"))


@app.route("/rooms/<int:room_id>/update", methods=["POST"])
@admin_required
def update_room(room_id):
    try:
        db().execute("UPDATE rooms SET room_type=?,monthly_rate=?,capacity=?,status=?,notes=? WHERE id=?",
            (request.form["room_type"], float(request.form["monthly_rate"]), max(1,int(request.form["capacity"])),
             request.form["status"], request.form.get("notes","").strip(), room_id))
        db().commit()
        flash("Room updated.", "success")
    except (ValueError, KeyError):
        flash("Invalid room details.", "danger")
    return redirect(url_for("rooms"))

@app.route("/tenants")
@admin_required
def tenants():
    rows = db().execute("""SELECT t.*, r.room_number FROM tenants t LEFT JOIN rooms r ON t.room_id=r.id
                           ORDER BY t.id DESC""").fetchall()
    available = db().execute("SELECT * FROM rooms WHERE status='Available' ORDER BY room_number").fetchall()
    return render_template("tenants.html", tenants=rows, rooms=available)

@app.route("/tenants/add", methods=["POST"])
@admin_required
def add_tenant():
    try:
        name=request.form["full_name"].strip()
        email=request.form.get("email","").strip().lower()
        phone=request.form.get("phone","").strip()
        room_id=request.form.get("room_id") or None
        move_in=request.form.get("move_in") or None
        if not name: raise ValueError()
        db().execute("""INSERT INTO tenants(full_name,email,phone,room_id,move_in,status,emergency_contact,notes)
                        VALUES(?,?,?,?,?,?,?,?)""",
            (name,email,phone,room_id,move_in,request.form.get("status","Active"),
             request.form.get("emergency_contact","").strip(),request.form.get("notes","").strip()))
        db().commit()
        flash("Tenant added.", "success")
    except (ValueError, sqlite3.IntegrityError, KeyError):
        flash("Could not add tenant. Check the details and selected room.", "danger")
    return redirect(url_for("tenants"))

@app.route("/tenants/<int:tenant_id>/update", methods=["POST"])
@admin_required
def update_tenant(tenant_id):
    try:
        db().execute("""UPDATE tenants SET full_name=?,email=?,phone=?,room_id=?,move_in=?,move_out=?,status=?,
                        emergency_contact=?,notes=? WHERE id=?""",
            (request.form["full_name"].strip(), request.form.get("email","").strip(),
             request.form.get("phone","").strip(), request.form.get("room_id") or None,
             request.form.get("move_in") or None, request.form.get("move_out") or None,
             request.form["status"], request.form.get("emergency_contact","").strip(),
             request.form.get("notes","").strip(), tenant_id))
        db().commit()
        flash("Tenant updated.", "success")
    except (ValueError, KeyError, sqlite3.IntegrityError):
        flash("Could not update tenant.", "danger")
    return redirect(url_for("tenants"))

@app.route("/tenants/<int:tenant_id>/check-in", methods=["POST"])
@admin_required
def tenant_check_in(tenant_id):
    conn = db()
    tenant = conn.execute("SELECT * FROM tenants WHERE id=?", (tenant_id,)).fetchone()
    if not tenant or not tenant["room_id"]:
        flash("Assign a room before checking in.", "warning")
    else:
        conn.execute("UPDATE tenants SET status='Active', move_in=COALESCE(move_in, ?), move_out=NULL WHERE id=?",
                     (date.today().isoformat(), tenant_id))
        conn.commit()
        flash("Tenant checked in.", "success")
    return redirect(url_for("tenants"))

@app.route("/tenants/<int:tenant_id>/check-out", methods=["POST"])
@admin_required
def tenant_check_out(tenant_id):
    conn = db()
    conn.execute("UPDATE tenants SET status='Moved Out', move_out=? WHERE id=?",
                 (date.today().isoformat(), tenant_id))
    conn.commit()
    flash("Tenant checked out.", "success")
    return redirect(url_for("tenants"))

@app.route("/payments")
@login_required
def payments():
    conn = db()
    if session["user"]["role"] == "admin":
        rows = conn.execute("""SELECT p.*,t.full_name,r.room_number FROM payments p JOIN tenants t ON p.tenant_id=t.id
                               LEFT JOIN rooms r ON t.room_id=r.id ORDER BY p.payment_date DESC,p.id DESC""").fetchall()
        tenant_options = conn.execute("SELECT id,full_name FROM tenants ORDER BY full_name").fetchall()
    else:
        tenant = current_tenant()
        rows = conn.execute("SELECT * FROM payments WHERE tenant_id=? ORDER BY payment_date DESC,payment_date DESC", (tenant["id"],)).fetchall() if tenant else []
        tenant_options = []
    tenant = current_tenant() if session["user"]["role"] != "admin" else None
    room = db().execute("SELECT * FROM rooms WHERE id=?", (tenant["room_id"],)).fetchone() if tenant and tenant["room_id"] else None
    return render_template("payments.html", payments=rows, tenants=tenant_options,
                           is_admin=session["user"]["role"]=="admin", tenant=tenant, room=room)

@app.route("/payments/demo", methods=["POST"])
@login_required
def demo_payment():
    """Record a clearly labeled simulated payment; no external gateway or money transfer."""
    if session["user"]["role"] == "admin":
        flash("Demo rent payments are available from a tenant account.", "warning")
        return redirect(url_for("payments"))
    tenant = current_tenant()
    if not tenant or not tenant["room_id"]:
        flash("Choose a room before making a rent payment.", "warning")
        return redirect(url_for("rooms"))
    room = db().execute("SELECT * FROM rooms WHERE id=?", (tenant["room_id"],)).fetchone()
    if not room:
        flash("Your assigned room could not be found. Contact the administrator.", "danger")
        return redirect(url_for("dashboard"))
    method = request.form.get("method", "Demo GCash")
    allowed_methods = {"Demo GCash", "Demo Maya", "Demo Cash"}
    if method not in allowed_methods:
        method = "Demo GCash"
    period = request.form.get("period", date.today().strftime("%Y-%m"))
    try:
        # HTML month input normally sends YYYY-MM; validate before saving.
        datetime.strptime(period, "%Y-%m")
    except (TypeError, ValueError):
        flash("Select a valid billing month.", "danger")
        return redirect(url_for("payments"))
    existing = db().execute("SELECT id FROM payments WHERE tenant_id=? AND period=? AND status='Paid'",
                            (tenant["id"], period)).fetchone()
    if existing:
        flash("A paid rent record already exists for that billing month.", "warning")
        return redirect(url_for("payments"))
    reference = "DEMO-" + datetime.now().strftime("%Y%m%d%H%M%S%f")
    db().execute("""INSERT INTO payments(tenant_id,amount,payment_date,period,method,reference,status,notes)
                    VALUES(?,?,?,?,?,?,?,?)""",
                 (tenant["id"], room["monthly_rate"], date.today().isoformat(), period,
                  method, reference, "Paid", "DEMO ONLY: simulated payment; no real money was transferred."))
    db().commit()
    flash("Demo payment recorded successfully. No real money was charged or transferred.", "success")
    return redirect(url_for("payments"))

@app.route("/payments/add", methods=["POST"])
@admin_required
def add_payment():
    try:
        amount=float(request.form["amount"])
        if amount <= 0: raise ValueError()
        db().execute("""INSERT INTO payments(tenant_id,amount,payment_date,period,method,reference,status,notes)
                        VALUES(?,?,?,?,?,?,?,?)""",
            (int(request.form["tenant_id"]), amount, request.form["payment_date"], request.form["period"],
             request.form["method"], request.form.get("reference","").strip(), request.form["status"],
             request.form.get("notes","").strip()))
        db().commit()
        flash("Payment record saved.", "success")
    except (ValueError, KeyError, sqlite3.IntegrityError):
        flash("Could not save payment. Check the amount and tenant.", "danger")
    return redirect(url_for("payments"))

@app.route("/payments/<int:payment_id>/status", methods=["POST"])
@admin_required
def payment_status(payment_id):
    status=request.form.get("status")
    if status in ("Paid","Unpaid","Partial"):
        db().execute("UPDATE payments SET status=? WHERE id=?", (status,payment_id))
        db().commit()
        flash("Payment status updated.", "success")
    return redirect(url_for("payments"))

@app.route("/maintenance")
@login_required
def maintenance():
    conn=db()
    is_admin=session["user"]["role"]=="admin"
    if is_admin:
        rows=conn.execute("""SELECT m.*,t.full_name,r.room_number FROM maintenance m
                             LEFT JOIN tenants t ON m.tenant_id=t.id LEFT JOIN rooms r ON m.room_id=r.id
                             ORDER BY CASE m.status WHEN 'Open' THEN 0 WHEN 'In Progress' THEN 1 ELSE 2 END,m.created_at DESC""").fetchall()
    else:
        tenant=current_tenant()
        rows=conn.execute("""SELECT m.*,r.room_number FROM maintenance m LEFT JOIN rooms r ON m.room_id=r.id
                             WHERE m.tenant_id=? ORDER BY m.created_at DESC""",(tenant["id"],)).fetchall() if tenant else []
    rooms_list=conn.execute("SELECT id,room_number FROM rooms ORDER BY room_number").fetchall()
    return render_template("maintenance.html", requests=rows, rooms=rooms_list, is_admin=is_admin)

@app.route("/maintenance/add", methods=["POST"])
@login_required
def add_maintenance():
    title=request.form.get("title","").strip()
    if not title:
        flash("Please enter a request title.", "danger")
        return redirect(url_for("maintenance"))
    is_admin=session["user"]["role"]=="admin"
    tenant_id=request.form.get("tenant_id") if is_admin else (current_tenant()["id"] if current_tenant() else None)
    room_id=request.form.get("room_id") or None
    db().execute("""INSERT INTO maintenance(tenant_id,room_id,title,description,priority,status)
                    VALUES(?,?,?,?,?,'Open')""",
        (tenant_id,room_id,title,request.form.get("description","").strip(),
         request.form.get("priority","Normal")))
    db().commit()
    flash("Maintenance request submitted.", "success")
    return redirect(url_for("maintenance"))

@app.route("/maintenance/<int:req_id>/status", methods=["POST"])
@admin_required
def maintenance_status(req_id):
    status=request.form.get("status")
    if status in ("Open","In Progress","Resolved"):
        resolved=datetime.now().isoformat(timespec="seconds") if status=="Resolved" else ""
        db().execute("UPDATE maintenance SET status=?,resolved_at=? WHERE id=?", (status,resolved,req_id))
        db().commit()
        flash("Request status updated.", "success")
    return redirect(url_for("maintenance"))

@app.route("/reports")
@admin_required
def reports():
    conn=db()
    monthly=conn.execute("""SELECT substr(payment_date,1,7) AS month,
        SUM(CASE WHEN status='Paid' THEN amount ELSE 0 END) AS collected,
        SUM(CASE WHEN status!='Paid' THEN amount ELSE 0 END) AS outstanding
        FROM payments GROUP BY substr(payment_date,1,7) ORDER BY month DESC LIMIT 12""").fetchall()
    occupancy=conn.execute("""SELECT r.room_number,r.room_type,r.monthly_rate,r.capacity,
        SUM(CASE WHEN t.status='Active' THEN 1 ELSE 0 END) AS occupants
        FROM rooms r LEFT JOIN tenants t ON t.room_id=r.id GROUP BY r.id ORDER BY r.room_number""").fetchall()
    return render_template("reports.html", monthly=monthly, occupancy=occupancy)

@app.route("/account", methods=["GET","POST"])
@login_required
def account():
    if request.method=="POST":
        name=request.form.get("name","").strip()
        old=request.form.get("current_password","")
        new=request.form.get("new_password","")
        user=db().execute("SELECT * FROM users WHERE id=?",(session["user"]["id"],)).fetchone()
        if not name:
            flash("Name cannot be blank.", "danger")
        elif new and (len(new)<8 or not check_password_hash(user["password_hash"],old)):
            flash("To change your password, enter your current password and a new password of at least 8 characters.", "danger")
        else:
            if new:
                db().execute("UPDATE users SET name=?,password_hash=? WHERE id=?",
                             (name,generate_password_hash(new),user["id"]))
            else:
                db().execute("UPDATE users SET name=? WHERE id=?",(name,user["id"]))
            db().commit()
            session["user"]["name"]=name
            flash("Account updated.", "success")
            return redirect(url_for("account"))
    return render_template("account.html")

if __name__ == "__main__":
    init_db()
    app.run(debug=os.environ.get("FLASK_DEBUG") == "1")
