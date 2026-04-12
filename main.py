from fastapi import FastAPI, Depends, HTTPException, status, Query
from fastapi.responses import HTMLResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from pydantic import BaseModel
import psycopg2
import psycopg2.extras
import os
import json
import secrets
from datetime import datetime
import pytz

app = FastAPI()
security = HTTPBasic()

DB_URL = os.environ.get("DATABASE_URL", "")
DASHBOARD_USER = os.environ.get("DASHBOARD_USER", "admin")
DASHBOARD_PASS = os.environ.get("DASHBOARD_PASS", "retell2024")
TZ = pytz.timezone("Europe/Istanbul")


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
            detail="Yetkisiz erisim",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username


@app.get("/api/stats")
def get_stats(conn=Depends(get_db), _=Depends(verify)):
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("""
        SELECT
            COUNT(*) FILTER (WHERE DATE(started_at AT TIME ZONE 'Europe/Istanbul') = CURRENT_DATE) as bugun_arama,
            COUNT(*) as toplam_arama
        FROM calls
    """)
    call_stats = dict(cur.fetchone())
    cur.execute("""
        SELECT
            COUNT(*) FILTER (WHERE DATE(created_at AT TIME ZONE 'Europe/Istanbul') = CURRENT_DATE) as bugun_siparis,
            COUNT(*) FILTER (WHERE status = 'new' AND archived_at IS NULL) as yeni_siparis,
            COUNT(*) FILTER (WHERE status = 'shipped' AND archived_at IS NULL) as kargoda,
            COUNT(*) FILTER (WHERE status = 'cancelled' AND archived_at IS NULL) as iptal
        FROM orders
    """)
    order_stats = dict(cur.fetchone())
    cur.execute("SELECT COUNT(*) FILTER (WHERE DATE(first_call_at AT TIME ZONE 'Europe/Istanbul') = CURRENT_DATE) as yeni_musteri FROM customers")
    musteri_stats = dict(cur.fetchone())
    now_istanbul = datetime.now(TZ)
    return {**call_stats, **order_stats, **musteri_stats, "server_time": now_istanbul.strftime("%H:%M")}


@app.get("/api/calls")
def get_calls(limit: int = 50, search: str = "", conn=Depends(get_db), _=Depends(verify)):
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    if search:
        cur.execute("""
            SELECT c.call_id, c.phone_number, cu.name, cu.surname,
                   c.started_at, c.ended_at, c.duration_s, c.status,
                   c.recording_url, c.summary
            FROM calls c
            LEFT JOIN customers cu ON c.phone_number = cu.phone_number
            WHERE c.phone_number ILIKE %s OR cu.name ILIKE %s OR cu.surname ILIKE %s
            ORDER BY c.started_at DESC LIMIT %s
        """, (f"%{search}%", f"%{search}%", f"%{search}%", limit))
    else:
        cur.execute("""
            SELECT c.call_id, c.phone_number, cu.name, cu.surname,
                   c.started_at, c.ended_at, c.duration_s, c.status,
                   c.recording_url, c.summary
            FROM calls c
            LEFT JOIN customers cu ON c.phone_number = cu.phone_number
            ORDER BY c.started_at DESC LIMIT %s
        """, (limit,))
    rows = cur.fetchall()
    result = []
    for row in rows:
        d = dict(row)
        try: d["summary_data"] = json.loads(d["summary"]) if d.get("summary") else {}
        except: d["summary_data"] = {}
        result.append(d)
    return result


@app.get("/api/calls/{call_id}")
def get_call_detail(call_id: str, conn=Depends(get_db), _=Depends(verify)):
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("""
        SELECT c.*, cu.name, cu.surname, cu.address
        FROM calls c LEFT JOIN customers cu ON c.phone_number = cu.phone_number
        WHERE c.call_id = %s
    """, (call_id,))
    call = cur.fetchone()
    if not call: raise HTTPException(status_code=404, detail="Bulunamadi")
    call = dict(call)
    try: call["summary_data"] = json.loads(call["summary"]) if call.get("summary") else {}
    except: call["summary_data"] = {}
    cur.execute("SELECT role, content, spoken_at FROM transcripts WHERE call_id = %s ORDER BY spoken_at", (call_id,))
    call["transcripts"] = [dict(r) for r in cur.fetchall()]
    return call


@app.get("/api/customers")
def get_customers(search: str = "", conn=Depends(get_db), _=Depends(verify)):
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    if search:
        cur.execute("""
            SELECT * FROM customers
            WHERE phone_number ILIKE %s OR name ILIKE %s OR surname ILIKE %s OR address ILIKE %s
            ORDER BY last_call_at DESC NULLS LAST
        """, (f"%{search}%", f"%{search}%", f"%{search}%", f"%{search}%"))
    else:
        cur.execute("SELECT * FROM customers ORDER BY last_call_at DESC NULLS LAST")
    return [dict(r) for r in cur.fetchall()]


@app.get("/api/orders")
def get_orders(search: str = "", archive: bool = False, conn=Depends(get_db), _=Depends(verify)):
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    archive_filter = "AND archived_at IS NOT NULL" if archive else "AND archived_at IS NULL"
    if search:
        cur.execute(f"""
            SELECT o.*, cu.name as customer_name, cu.surname as customer_surname
            FROM orders o
            LEFT JOIN customers cu ON o.phone_number = cu.phone_number
            WHERE (o.phone_number ILIKE %s OR o.name ILIKE %s OR o.surname ILIKE %s
                   OR o.address ILIKE %s OR o.siparis_detayi ILIKE %s
                   OR CAST(o.created_at AT TIME ZONE 'Europe/Istanbul' AS TEXT) ILIKE %s)
            {archive_filter}
            ORDER BY o.created_at DESC
        """, (f"%{search}%", f"%{search}%", f"%{search}%", f"%{search}%", f"%{search}%", f"%{search}%"))
    else:
        cur.execute(f"""
            SELECT o.*, cu.name as customer_name, cu.surname as customer_surname
            FROM orders o
            LEFT JOIN customers cu ON o.phone_number = cu.phone_number
            WHERE 1=1 {archive_filter}
            ORDER BY o.created_at DESC
        """)
    return [dict(r) for r in cur.fetchall()]


class OrderStatusUpdate(BaseModel):
    status: str


@app.patch("/api/orders/{order_id}/status")
def update_order_status(order_id: str, body: OrderStatusUpdate, conn=Depends(get_db), _=Depends(verify)):
    allowed = ['new', 'cancelled', 'postponed', 'shipped']
    if body.status not in allowed:
        raise HTTPException(status_code=400, detail="Gecersiz durum")

    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT status FROM orders WHERE id = %s", (order_id,))
    order = cur.fetchone()
    if not order: raise HTTPException(status_code=404, detail="Siparis bulunamadi")

    current = order['status']
    if current == 'cancelled': raise HTTPException(status_code=400, detail="Iptal edilen siparis degistirilemez")
    if current == 'shipped' and body.status == 'cancelled': raise HTTPException(status_code=400, detail="Kargodaki siparis iptal edilemez")

    cur.execute("UPDATE orders SET status = %s, updated_at = NOW() WHERE id = %s", (body.status, order_id))
    conn.commit()
    return {"success": True, "status": body.status}


@app.post("/api/orders/close-day")
def close_day(conn=Depends(get_db), _=Depends(verify)):
    """Günü kapat - aktif siparişleri arşivle"""
    now_istanbul = datetime.now(TZ)
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("""
        UPDATE orders
        SET archived_at = NOW(), archive_date = CURRENT_DATE
        WHERE archived_at IS NULL AND status IN ('new', 'shipped', 'postponed', 'cancelled')
    """)
    affected = cur.rowcount
    conn.commit()
    return {"success": True, "archived": affected, "date": now_istanbul.strftime("%d.%m.%Y %H:%M")}


@app.get("/api/analytics")
def get_analytics(period: str = "daily", conn=Depends(get_db), _=Depends(verify)):
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    if period == "daily":
        cur.execute("""
            SELECT DATE(created_at AT TIME ZONE 'Europe/Istanbul') as tarih,
                   COUNT(*) as toplam,
                   COUNT(*) FILTER (WHERE status = 'shipped') as kargolanan,
                   COUNT(*) FILTER (WHERE status = 'cancelled') as iptal
            FROM orders GROUP BY DATE(created_at AT TIME ZONE 'Europe/Istanbul') ORDER BY tarih DESC LIMIT 30
        """)
    elif period == "weekly":
        cur.execute("""
            SELECT DATE_TRUNC('week', created_at AT TIME ZONE 'Europe/Istanbul') as tarih,
                   COUNT(*) as toplam,
                   COUNT(*) FILTER (WHERE status = 'shipped') as kargolanan,
                   COUNT(*) FILTER (WHERE status = 'cancelled') as iptal
            FROM orders GROUP BY 1 ORDER BY tarih DESC LIMIT 12
        """)
    elif period == "monthly":
        cur.execute("""
            SELECT DATE_TRUNC('month', created_at AT TIME ZONE 'Europe/Istanbul') as tarih,
                   COUNT(*) as toplam,
                   COUNT(*) FILTER (WHERE status = 'shipped') as kargolanan,
                   COUNT(*) FILTER (WHERE status = 'cancelled') as iptal
            FROM orders GROUP BY 1 ORDER BY tarih DESC LIMIT 12
        """)
    return [dict(r) for r in cur.fetchall()]


@app.get("/", response_class=HTMLResponse)
def dashboard(_=Depends(verify)):
    with open("index.html", "r", encoding="utf-8") as f:
        return f.read()



class CustomerCreate(BaseModel):
    phone_number: str
    name: str = ""
    surname: str = ""
    address: str = ""

class CustomerUpdate(BaseModel):
    name: str = ""
    surname: str = ""
    address: str = ""

class OrderCreate(BaseModel):
    phone_number: str
    name: str = ""
    surname: str = ""
    address: str = ""
    siparis_detayi: str
    tutar: str = ""
    odeme_yontemi: str = "Kapida odeme"
    status: str = "new"

@app.post("/api/customers")
def create_customer(body: CustomerCreate, conn=Depends(get_db), _=Depends(verify)):
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT phone_number FROM customers WHERE phone_number = %s", (body.phone_number,))
    if cur.fetchone():
        raise HTTPException(status_code=400, detail="Bu telefon numarasi zaten kayitli")
    cur.execute("INSERT INTO customers (phone_number, name, surname, address) VALUES (%s, %s, %s, %s)", (body.phone_number, body.name, body.surname, body.address))
    conn.commit()
    return {"success": True}

@app.patch("/api/customers/{phone_number}")
def update_customer(phone_number: str, body: CustomerUpdate, conn=Depends(get_db), _=Depends(verify)):
    cur = conn.cursor()
    cur.execute("UPDATE customers SET name=%s, surname=%s, address=%s WHERE phone_number=%s", (body.name, body.surname, body.address, phone_number))
    conn.commit()
    return {"success": True}

@app.post("/api/orders")
def create_order(body: OrderCreate, conn=Depends(get_db), _=Depends(verify)):
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("INSERT INTO orders (phone_number, name, surname, address, siparis_detayi, tutar, odeme_yontemi, status, created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,NOW()) RETURNING id", (body.phone_number, body.name, body.surname, body.address, body.siparis_detayi, body.tutar, body.odeme_yontemi, body.status))
    conn.commit()
    return {"success": True}


@app.delete("/api/customers/{phone_number}")
def delete_customer(phone_number: str, conn=Depends(get_db), _=Depends(verify)):
    cur = conn.cursor()
    cur.execute("DELETE FROM transcripts WHERE call_id IN (SELECT call_id FROM calls WHERE phone_number = %s)", (phone_number,))
    cur.execute("DELETE FROM orders WHERE phone_number = %s", (phone_number,))
    cur.execute("DELETE FROM calls WHERE phone_number = %s", (phone_number,))
    cur.execute("DELETE FROM customers WHERE phone_number = %s", (phone_number,))
    conn.commit()
    return {"success": True}


# URUN ENDPOINTS
class ProductCreate(BaseModel):
    name: str
    category: str
    subcategory: str = ""
    weight: float = 0
    unit: str = "kg"
    price_cod: float = 0
    price_eft: float = 0
    description: str = ""
    active: bool = True

class ProductUpdate(BaseModel):
    name: str = ""
    subcategory: str = ""
    weight: float = 0
    unit: str = "kg"
    price_cod: float = 0
    price_eft: float = 0
    description: str = ""
    active: bool = True

@app.get("/api/products")
def get_products(category: str = "", conn=Depends(get_db), _=Depends(verify)):
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    if category:
        cur.execute("SELECT * FROM products WHERE category = %s ORDER BY subcategory, weight", (category,))
    else:
        cur.execute("SELECT * FROM products ORDER BY category, subcategory, weight")
    return [dict(r) for r in cur.fetchall()]

@app.post("/api/products")
def create_product(body: ProductCreate, conn=Depends(get_db), _=Depends(verify)):
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("INSERT INTO products (name, category, subcategory, weight, unit, price_cod, price_eft, description, active) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
        (body.name, body.category, body.subcategory, body.weight, body.unit, body.price_cod, body.price_eft, body.description, body.active))
    conn.commit()
    return {"success": True}

@app.patch("/api/products/{product_id}")
def update_product(product_id: str, body: ProductUpdate, conn=Depends(get_db), _=Depends(verify)):
    cur = conn.cursor()
    cur.execute("UPDATE products SET name=%s, subcategory=%s, weight=%s, unit=%s, price_cod=%s, price_eft=%s, description=%s, active=%s, updated_at=NOW() WHERE id=%s",
        (body.name, body.subcategory, body.weight, body.unit, body.price_cod, body.price_eft, body.description, body.active, product_id))
    conn.commit()
    return {"success": True}

@app.delete("/api/products/{product_id}")
def delete_product(product_id: str, conn=Depends(get_db), _=Depends(verify)):
    cur = conn.cursor()
    cur.execute("DELETE FROM products WHERE id = %s", (product_id,))
    conn.commit()
    return {"success": True}
