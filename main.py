from fastapi import FastAPI, Depends, HTTPException, status
from fastapi.responses import HTMLResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles
import psycopg2
import psycopg2.extras
import os
import json
import secrets

app = FastAPI()
security = HTTPBasic()

DB_URL = os.environ.get("DATABASE_URL", "")
DASHBOARD_USER = os.environ.get("DASHBOARD_USER", "admin")
DASHBOARD_PASS = os.environ.get("DASHBOARD_PASS", "retell2024")


def get_db():
    conn = psycopg2.connect(DB_URL)
    try:
        yield conn
    finally:
        conn.close()


def verify(credentials: HTTPBasicCredentials = Depends(security)):
    ok_user = secrets.compare_digest(credentials.username, DASHBOARD_USER)
    ok_pass = secrets.compare_digest(credentials.password, DASHBOARD_PASS)
    if not (ok_user and ok_pass):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Yetkisiz erişim",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username


@app.get("/api/stats")
def get_stats(conn=Depends(get_db), _=Depends(verify)):
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("""
        SELECT
            COUNT(*) FILTER (WHERE DATE(started_at) = CURRENT_DATE) as bugun_arama,
            COUNT(*) FILTER (WHERE DATE(started_at) = CURRENT_DATE AND summary::jsonb->>'siparis_var_mi' = 'true') as bugun_siparis,
            COUNT(DISTINCT phone_number) FILTER (WHERE DATE(first_call_at) = CURRENT_DATE) as yeni_musteri,
            COUNT(*) as toplam_arama
        FROM calls
        LEFT JOIN customers USING (phone_number)
    """)
    stats = dict(cur.fetchone())
    return stats


@app.get("/api/calls")
def get_calls(limit: int = 50, conn=Depends(get_db), _=Depends(verify)):
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("""
        SELECT
            c.call_id,
            c.phone_number,
            cu.name,
            cu.surname,
            c.started_at,
            c.ended_at,
            c.duration_s,
            c.status,
            c.recording_url,
            c.summary
        FROM calls c
        LEFT JOIN customers cu ON c.phone_number = cu.phone_number
        ORDER BY c.started_at DESC
        LIMIT %s
    """, (limit,))
    rows = cur.fetchall()
    result = []
    for row in rows:
        d = dict(row)
        if d.get("summary"):
            try:
                d["summary_data"] = json.loads(d["summary"])
            except:
                d["summary_data"] = {}
        else:
            d["summary_data"] = {}
        result.append(d)
    return result


@app.get("/api/calls/{call_id}")
def get_call_detail(call_id: str, conn=Depends(get_db), _=Depends(verify)):
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("""
        SELECT c.*, cu.name, cu.surname, cu.address
        FROM calls c
        LEFT JOIN customers cu ON c.phone_number = cu.phone_number
        WHERE c.call_id = %s
    """, (call_id,))
    call = cur.fetchone()
    if not call:
        raise HTTPException(status_code=404, detail="Arama bulunamadı")
    call = dict(call)
    if call.get("summary"):
        try:
            call["summary_data"] = json.loads(call["summary"])
        except:
            call["summary_data"] = {}

    cur.execute("""
        SELECT role, content, spoken_at FROM transcripts
        WHERE call_id = %s ORDER BY spoken_at
    """, (call_id,))
    call["transcripts"] = [dict(r) for r in cur.fetchall()]
    return call


@app.get("/api/customers")
def get_customers(conn=Depends(get_db), _=Depends(verify)):
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("""
        SELECT * FROM customers ORDER BY last_call_at DESC NULLS LAST
    """)
    return [dict(r) for r in cur.fetchall()]


@app.get("/api/orders")
def get_orders(conn=Depends(get_db), _=Depends(verify)):
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("""
        SELECT
            c.call_id,
            c.phone_number,
            cu.name,
            cu.surname,
            c.started_at,
            c.summary
        FROM calls c
        LEFT JOIN customers cu ON c.phone_number = cu.phone_number
        WHERE c.summary IS NOT NULL
        AND c.summary != ''
        AND (c.summary::jsonb->>'siparis_var_mi')::boolean = true
        ORDER BY c.started_at DESC
    """)
    rows = cur.fetchall()
    result = []
    for row in rows:
        d = dict(row)
        try:
            sd = json.loads(d["summary"])
            d["siparis_detayi"] = sd.get("siparis_detayi", "")
            d["tutar"] = sd.get("tutar", "")
            d["odeme_yontemi"] = sd.get("odeme_yontemi", "")
            d["adres"] = sd.get("adres", "")
        except:
            pass
        result.append(d)
    return result


@app.get("/", response_class=HTMLResponse)
def dashboard(_=Depends(verify)):
    with open("index.html", "r", encoding="utf-8") as f:
        return f.read()
