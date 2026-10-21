import os, sqlite3, hashlib, json, secrets
from datetime import datetime
from functools import wraps
from flask import Flask, render_template, request, redirect, url_for, session, flash

BASE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(BASE, "voting.db")

app = Flask(__name__)
app.secret_key = "blockvote-demo-secret-2026"

def db():
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    return con

def hash_block(index, timestamp, payload, previous):
    raw = f"{index}{timestamp}{payload}{previous}".encode()
    return hashlib.sha256(raw).hexdigest()

def init_db():
    with db() as con:
        con.executescript("""
        CREATE TABLE IF NOT EXISTS users(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            voter_id TEXT UNIQUE NOT NULL,
            email TEXT NOT NULL,
            password TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS candidates(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            party TEXT NOT NULL,
            symbol TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS votes(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER UNIQUE NOT NULL,
            candidate_id INTEGER NOT NULL,
            txid TEXT UNIQUE NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS blocks(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            block_no INTEGER UNIQUE NOT NULL,
            timestamp TEXT NOT NULL,
            payload TEXT NOT NULL,
            previous_hash TEXT NOT NULL,
            block_hash TEXT NOT NULL
        );
        """)

        if con.execute("SELECT COUNT(*) FROM candidates").fetchone()[0] == 0:
            con.executemany(
                "INSERT INTO candidates(name,party,symbol) VALUES(?,?,?)",
                [
                    ("Arun Kumar", "Progressive Students", "★"),
                    ("Divya Priya", "Student Welfare", "◆"),
                    ("Kavin Raj", "Youth Voice", "●"),
                ],
            )

        if con.execute("SELECT COUNT(*) FROM blocks").fetchone()[0] == 0:
            ts = datetime.now().isoformat(timespec="seconds")
            payload = json.dumps({"type":"GENESIS"}, sort_keys=True)
            h = hash_block(0, ts, payload, "0"*64)
            con.execute(
                "INSERT INTO blocks(block_no,timestamp,payload,previous_hash,block_hash) VALUES(?,?,?,?,?)",
                (0, ts, payload, "0"*64, h)
            )

def add_block(payload):
    with db() as con:
        last = con.execute("SELECT * FROM blocks ORDER BY block_no DESC LIMIT 1").fetchone()
        no = last["block_no"] + 1
        ts = datetime.now().isoformat(timespec="seconds")
        data = json.dumps(payload, sort_keys=True)
        h = hash_block(no, ts, data, last["block_hash"])
        con.execute(
            "INSERT INTO blocks(block_no,timestamp,payload,previous_hash,block_hash) VALUES(?,?,?,?,?)",
            (no, ts, data, last["block_hash"], h)
        )

def chain_valid():
    with db() as con:
        blocks = con.execute("SELECT * FROM blocks ORDER BY block_no").fetchall()
    if not blocks:
        return False
    for i, b in enumerate(blocks):
        prev = "0"*64 if i == 0 else blocks[i-1]["block_hash"]
        if b["previous_hash"] != prev:
            return False
        expected = hash_block(b["block_no"], b["timestamp"], b["payload"], b["previous_hash"])
        if expected != b["block_hash"]:
            return False
    return True

def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return wrapper

@app.route("/")
def home():
    with db() as con:
        candidates = con.execute("SELECT * FROM candidates").fetchall()
        voters = con.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        votes = con.execute("SELECT COUNT(*) FROM votes").fetchone()[0]
    return render_template("index.html", candidates=candidates, voters=voters, votes=votes, valid=chain_valid())

@app.route("/register", methods=["GET","POST"])
def register():
    if request.method == "POST":
        name = request.form["name"].strip()
        voter_id = request.form["voter_id"].strip().upper()
        email = request.form["email"].strip().lower()
        password = request.form["password"]
        if len(password) < 8:
            flash("Password must be at least 8 characters.", "error")
            return render_template("register.html")
        try:
            with db() as con:
                con.execute(
                    "INSERT INTO users(name,voter_id,email,password) VALUES(?,?,?,?)",
                    (name, voter_id, email, password)
                )
            flash("Registration successful. Login now.", "success")
            return redirect(url_for("login"))
        except sqlite3.IntegrityError:
            flash("This Voter ID already exists. Use another Voter ID.", "error")
    return render_template("register.html")

@app.route("/login", methods=["GET","POST"])
def login():
    if request.method == "POST":
        voter_id = request.form["voter_id"].strip().upper()
        password = request.form["password"]
        with db() as con:
            user = con.execute("SELECT * FROM users WHERE voter_id=?", (voter_id,)).fetchone()
        if user and user["password"] == password:
            session.clear()
            session["user_id"] = user["id"]
            session["name"] = user["name"]
            return redirect(url_for("dashboard"))
        flash("Invalid Voter ID or password.", "error")
    return render_template("login.html")

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("home"))

@app.route("/dashboard")
@login_required
def dashboard():
    with db() as con:
        user = con.execute("SELECT * FROM users WHERE id=?", (session["user_id"],)).fetchone()
        voted = con.execute("SELECT 1 FROM votes WHERE user_id=?", (session["user_id"],)).fetchone()
        candidates = con.execute("SELECT * FROM candidates").fetchall()
    return render_template("dashboard.html", user=user, voted=bool(voted), candidates=candidates)

@app.route("/vote/<int:candidate_id>", methods=["POST"])
@login_required
def vote(candidate_id):
    with db() as con:
        if con.execute("SELECT 1 FROM votes WHERE user_id=?", (session["user_id"],)).fetchone():
            flash("You have already voted.", "error")
            return redirect(url_for("results"))
        candidate = con.execute("SELECT * FROM candidates WHERE id=?", (candidate_id,)).fetchone()
        if not candidate:
            flash("Candidate not found.", "error")
            return redirect(url_for("dashboard"))
        txid = secrets.token_hex(16)
        ts = datetime.now().isoformat(timespec="seconds")
        con.execute(
            "INSERT INTO votes(user_id,candidate_id,txid,created_at) VALUES(?,?,?,?)",
            (session["user_id"], candidate_id, txid, ts)
        )
    add_block({"type":"VOTE","transaction":txid,"candidate_id":candidate_id})
    flash("Vote recorded successfully. Transaction: " + txid, "success")
    return redirect(url_for("results"))

@app.route("/results")
def results():
    with db() as con:
        rows = con.execute("""
            SELECT c.name,c.party,c.symbol,COUNT(v.id) AS votes
            FROM candidates c
            LEFT JOIN votes v ON v.candidate_id=c.id
            GROUP BY c.id ORDER BY votes DESC,c.id
        """).fetchall()
    max_votes = max([r["votes"] for r in rows], default=0)
    winners = [r["name"] for r in rows if r["votes"] == max_votes and max_votes > 0]
    return render_template("results.html", rows=rows, winners=winners, max_votes=max_votes)

@app.route("/blockchain")
def blockchain():
    with db() as con:
        blocks = con.execute("SELECT * FROM blocks ORDER BY block_no DESC").fetchall()
    return render_template("blockchain.html", blocks=blocks, valid=chain_valid())

if __name__ == "__main__":
    init_db()
    app.run(debug=True)
