from fastapi import FastAPI, HTTPException, Depends, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from pydantic import BaseModel
from typing import Optional
import psycopg2
import psycopg2.extras
import os
import secrets
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

app = FastAPI(title="SP Tech Solution - Billing API")

allowed_origins_env = os.getenv("ALLOWED_ORIGINS")
if allowed_origins_env:
    allowed_origins = [origin.strip() for origin in allowed_origins_env.split(",") if origin.strip()]
else:
    allowed_origins = [
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:5500",
        "http://127.0.0.1:5500",
        "null",
    ]

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

security = HTTPBasic()

ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "sptech@admin123")


def verify_admin(credentials: HTTPBasicCredentials = Depends(security)):
    correct_username = secrets.compare_digest(credentials.username, ADMIN_USERNAME)
    correct_password = secrets.compare_digest(credentials.password, ADMIN_PASSWORD)
    if not (correct_username and correct_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username


def _get_conn():
    return psycopg2.connect(
        host=os.getenv("DB_HOST"),
        database=os.getenv("DB_NAME"),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
        port=int(os.getenv("DB_PORT", "5432")),
        sslmode=os.getenv("DB_SSLMODE", "require"),
        connect_timeout=10,
    )


def get_db():
    conn = _get_conn()
    try:
        yield conn
    finally:
        conn.close()


def init_db():
    conn = _get_conn()
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS billing (
            id SERIAL PRIMARY KEY,
            customer_name VARCHAR(200) NOT NULL,
            phone VARCHAR(20) NOT NULL,
            address TEXT,
            product VARCHAR(300) NOT NULL,
            quantity INTEGER NOT NULL DEFAULT 1,
            rate NUMERIC(10, 2) NOT NULL,
            total NUMERIC(10, 2) NOT NULL,
            gst NUMERIC(10, 2) DEFAULT 0,
            grand_total NUMERIC(10, 2) NOT NULL,
            bill_date DATE NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            is_deleted BOOLEAN DEFAULT FALSE
        )
    """)
    conn.commit()
    cursor.close()
    conn.close()


@app.on_event("startup")
def startup():
    try:
        init_db()
        print("Database connected and table ready.")
    except Exception as exc:
        print(f"Database not reachable at startup: {exc}")


# ── Pydantic Models ──────────────────────────────────────────

class BillingCreate(BaseModel):
    customer_name: str
    phone: str
    address: Optional[str] = ""
    product: str
    quantity: int
    rate: float
    total: float
    gst: float = 0.0
    grand_total: float
    bill_date: str  # YYYY-MM-DD format


class BillingUpdate(BaseModel):
    customer_name: Optional[str] = None
    phone: Optional[str] = None
    address: Optional[str] = None
    product: Optional[str] = None
    quantity: Optional[int] = None
    rate: Optional[float] = None
    total: Optional[float] = None
    gst: Optional[float] = None
    grand_total: Optional[float] = None
    bill_date: Optional[str] = None


# ── Routes ───────────────────────────────────────────────────

@app.get("/")
def root():
    return {"message": "SP Tech Solution Billing API is running"}


@app.get("/auth/check", dependencies=[Depends(verify_admin)])
def auth_check():
    return {"message": "Authentication successful"}


@app.post("/billing", dependencies=[Depends(verify_admin)])
def create_bill(data: BillingCreate, conn=Depends(get_db)):
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO billing
            (customer_name, phone, address, product, quantity, rate, total, gst, grand_total, bill_date)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id
    """, (
        data.customer_name, data.phone, data.address,
        data.product, data.quantity, data.rate,
        data.total, data.gst, data.grand_total, data.bill_date
    ))
    new_id = cursor.fetchone()[0]
    conn.commit()
    cursor.close()
    return {"message": "Bill created successfully", "id": new_id}


@app.get("/billing", dependencies=[Depends(verify_admin)])
def get_all_bills(conn=Depends(get_db)):
    cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cursor.execute("""
        SELECT * FROM billing
        WHERE is_deleted = FALSE
        ORDER BY created_at DESC
    """)
    bills = cursor.fetchall()
    cursor.close()
    return bills


@app.get("/billing/{bill_id}", dependencies=[Depends(verify_admin)])
def get_bill(bill_id: int, conn=Depends(get_db)):
    cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cursor.execute("SELECT * FROM billing WHERE id = %s AND is_deleted = FALSE", (bill_id,))
    bill = cursor.fetchone()
    cursor.close()
    if not bill:
        raise HTTPException(status_code=404, detail="Bill not found")
    return bill


@app.put("/billing/{bill_id}", dependencies=[Depends(verify_admin)])
def update_bill(bill_id: int, data: BillingUpdate, conn=Depends(get_db)):
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM billing WHERE id = %s AND is_deleted = FALSE", (bill_id,))
    if not cursor.fetchone():
        cursor.close()
        raise HTTPException(status_code=404, detail="Bill not found")

    fields = {k: v for k, v in data.dict().items() if v is not None}
    if not fields:
        cursor.close()
        raise HTTPException(status_code=400, detail="No fields to update")

    set_clause = ", ".join([f"{k} = %s" for k in fields])
    values = list(fields.values()) + [bill_id]
    cursor.execute(f"UPDATE billing SET {set_clause} WHERE id = %s", values)
    conn.commit()
    cursor.close()
    return {"message": "Bill updated successfully"}


@app.delete("/billing/{bill_id}", dependencies=[Depends(verify_admin)])
def soft_delete_bill(bill_id: int, conn=Depends(get_db)):
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM billing WHERE id = %s AND is_deleted = FALSE", (bill_id,))
    if not cursor.fetchone():
        cursor.close()
        raise HTTPException(status_code=404, detail="Bill not found")

    cursor.execute("UPDATE billing SET is_deleted = TRUE WHERE id = %s", (bill_id,))
    conn.commit()
    cursor.close()
    return {"message": "Bill deleted successfully"}


@app.get("/billing/stats/summary", dependencies=[Depends(verify_admin)])
def get_summary(conn=Depends(get_db)):
    cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cursor.execute("""
        SELECT
            COUNT(*) AS total_bills,
            COALESCE(SUM(grand_total), 0) AS total_revenue,
            COALESCE(SUM(gst), 0) AS total_gst
        FROM billing
        WHERE is_deleted = FALSE
    """)
    summary = cursor.fetchone()
    cursor.close()
    return summary
