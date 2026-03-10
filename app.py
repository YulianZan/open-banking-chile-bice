from flask import Flask, render_template, request, jsonify, session, redirect, url_for
from functools import wraps
import sqlite3
import os
import json
from datetime import datetime, timedelta
from collections import defaultdict
import re

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "change-this-in-production")

DB_PATH = os.environ.get("DB_PATH", "/data/bank_data.db")
DASHBOARD_USER = os.environ.get("DASHBOARD_USER", "admin")
DASHBOARD_PASS = os.environ.get("DASHBOARD_PASS", "admin123")

# ── Categorización automática ─────────────────────────────────────────────────

CATEGORIES = {
    "Supermercado": ["LIDER", "JUMBO", "SANTA ISABEL", "UNIMARC", "TOTTUS", "ACUENTA", "SUPERMERCADO"],
    "Restaurante":  ["RESTAURANT", "SUSHI", "PIZZA", "BURGER", "MCDONALDS", "SUBWAY", "DOMINO"],
    "Transporte":   ["UBER", "CABIFY", "METRO", "TRANSANTIAGO", "SHELL", "COPEC", "PETROBRAS", "BENCIN"],
    "Farmacia":     ["FARMACIA", "CRUZ VERDE", "SALCOBRAND", "AHUMADA"],
    "Transferencia":["TRANSFERENCIA", "TEF ", "ABONO", "DEPOSITO"],
    "Comercio":     ["FALABELLA", "RIPLEY", "PARIS", "H&M", "ZARA"],
    "Servicios":    ["LUZ", "AGUA", "GAS", "INTERNET", "VTR", "ENTEL", "MOVISTAR", "CLARO"],
    "Salud":        ["CLINICA", "HOSPITAL", "MEDICO", "ISAPRE", "FONASA"],
    "Entretenimiento": ["NETFLIX", "SPOTIFY", "STEAM", "CINEMA", "CINE"],
}

def categorize(description: str) -> str:
    desc_upper = description.upper()
    for cat, keywords in CATEGORIES.items():
        if any(k in desc_upper for k in keywords):
            return cat
    return "Otros"

# ── Auth ──────────────────────────────────────────────────────────────────────

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("logged_in"):
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated

@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        user = request.form.get("username", "")
        pwd  = request.form.get("password", "")
        if user == DASHBOARD_USER and pwd == DASHBOARD_PASS:
            session["logged_in"] = True
            return redirect(url_for("index"))
        error = "Credenciales incorrectas"
    return render_template("login.html", error=error)

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

# ── DB helper ─────────────────────────────────────────────────────────────────

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

# ── Rutas principales ─────────────────────────────────────────────────────────

@app.route("/")
@login_required
def index():
    return render_template("dashboard.html")

# ── API endpoints ─────────────────────────────────────────────────────────────

@app.route("/api/summary")
@login_required
def api_summary():
    db = get_db()

    # Último saldo
    snap = db.execute(
        "SELECT balance, fetched_at FROM snapshots ORDER BY fetched_at DESC LIMIT 1"
    ).fetchone()

    # Movimientos del mes actual
    now = datetime.now()
    month_start = now.replace(day=1).strftime("%Y-%m-%d")

    rows = db.execute(
        "SELECT amount FROM movements WHERE fetched_at >= ?", (month_start,)
    ).fetchall()

    ingresos = sum(r["amount"] for r in rows if r["amount"] > 0)
    gastos   = sum(r["amount"] for r in rows if r["amount"] < 0)

    # Total movimientos
    total = db.execute("SELECT COUNT(*) as c FROM movements").fetchone()["c"]

    # Último run
    run = db.execute(
        "SELECT success, finished_at FROM runs ORDER BY finished_at DESC LIMIT 1"
    ).fetchone()

    db.close()
    return jsonify({
        "balance":    snap["balance"] if snap else None,
        "balance_at": snap["fetched_at"] if snap else None,
        "ingresos":   ingresos,
        "gastos":     gastos,
        "total_movements": total,
        "last_run_ok": bool(run["success"]) if run else None,
        "last_run_at": run["finished_at"] if run else None,
    })

@app.route("/api/movements")
@login_required
def api_movements():
    db = get_db()

    # Filtros
    search    = request.args.get("search", "")
    tipo      = request.args.get("tipo", "all")      # all | ingreso | gasto
    categoria = request.args.get("categoria", "all")
    mes       = request.args.get("mes", "")
    page      = int(request.args.get("page", 1))
    per_page  = 50

    query  = "SELECT * FROM movements WHERE 1=1"
    params = []

    if search:
        query += " AND description LIKE ?"
        params.append(f"%{search.upper()}%")

    if tipo == "ingreso":
        query += " AND amount > 0"
    elif tipo == "gasto":
        query += " AND amount < 0"

    if mes:
        # mes formato: YYYY-MM
        query += " AND strftime('%Y-%m', fetched_at) = ?"
        params.append(mes)

    # Obtener todos para filtrar por categoría en Python
    all_rows = db.execute(query + " ORDER BY date DESC, id DESC", params).fetchall()
    db.close()

    movements = []
    for r in all_rows:
        cat = categorize(r["description"])
        if categoria != "all" and cat != categoria:
            continue
        movements.append({
            "id":          r["id"],
            "date":        r["date"],
            "description": r["description"],
            "amount":      r["amount"],
            "balance":     r["balance"],
            "categoria":   cat,
            "fetched_at":  r["fetched_at"],
        })

    total = len(movements)
    start = (page - 1) * per_page
    paginated = movements[start:start + per_page]

    return jsonify({
        "movements": paginated,
        "total":     total,
        "page":      page,
        "pages":     (total + per_page - 1) // per_page,
    })

@app.route("/api/chart/monthly")
@login_required
def api_chart_monthly():
    db = get_db()
    rows = db.execute("""
        SELECT
            strftime('%Y-%m', date, 'unixepoch') as ym,
            substr(date, 7, 4) || '-' || substr(date, 4, 2) as month_key,
            SUM(CASE WHEN amount > 0 THEN amount ELSE 0 END) as ingresos,
            SUM(CASE WHEN amount < 0 THEN amount ELSE 0 END) as gastos
        FROM movements
        GROUP BY substr(date, 7, 4) || '-' || substr(date, 4, 2)
        ORDER BY substr(date, 7, 4) || '-' || substr(date, 4, 2) DESC
        LIMIT 6
    """).fetchall()
    db.close()

    data = list(reversed([{
        "month":    r["month_key"],
        "ingresos": round(r["ingresos"], 0),
        "gastos":   round(abs(r["gastos"]), 0),
    } for r in rows]))

    return jsonify(data)

@app.route("/api/chart/categories")
@login_required
def api_chart_categories():
    db = get_db()
    rows = db.execute(
        "SELECT description, amount FROM movements WHERE amount < 0"
    ).fetchall()
    db.close()

    cat_totals = defaultdict(float)
    for r in rows:
        cat = categorize(r["description"])
        cat_totals[cat] += abs(r["amount"])

    data = sorted(
        [{"categoria": k, "total": round(v, 0)} for k, v in cat_totals.items()],
        key=lambda x: x["total"], reverse=True
    )
    return jsonify(data)

@app.route("/api/months")
@login_required
def api_months():
    db = get_db()
    rows = db.execute("""
        SELECT DISTINCT substr(date, 7, 4) || '-' || substr(date, 4, 2) as m
        FROM movements
        ORDER BY m DESC
    """).fetchall()
    db.close()
    return jsonify([r["m"] for r in rows])

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
