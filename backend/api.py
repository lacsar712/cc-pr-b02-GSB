import os
from datetime import datetime, timedelta, timezone

import psycopg
from fastapi import Depends, FastAPI, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from passlib.context import CryptContext
from pydantic import BaseModel
from psycopg.rows import dict_row

DSN = os.environ.get("DATABASE_URL", "postgresql://app:app@localhost:54394/printreg")
SECRET = os.environ.get("JWT_SECRET", "print-register-dev-secret")
pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")
security = HTTPBearer(auto_error=False)
USERS = {
    "printer": {"role": "writer", "password_hash": pwd.hash("print123456")},
    "checker": {"role": "reader", "password_hash": pwd.hash("check123456")},
}


def connect():
    return psycopg.connect(DSN, row_factory=dict_row)


SCHEMA = [
    """
    CREATE TABLE IF NOT EXISTS jobs (
        id serial PRIMARY KEY,
        sheet text NOT NULL,
        machine text NOT NULL DEFAULT '',
        cyan_mm double precision NOT NULL,
        magenta_mm double precision NOT NULL,
        status text NOT NULL,
        verdict text NOT NULL DEFAULT '',
        reason text NOT NULL DEFAULT '',
        created_by text NOT NULL,
        created_at timestamptz NOT NULL
    )
    """,
    "ALTER TABLE jobs ADD COLUMN IF NOT EXISTS machine text NOT NULL DEFAULT ''",
    """
    CREATE TABLE IF NOT EXISTS machine_gates (
        machine text PRIMARY KEY,
        status text NOT NULL DEFAULT 'open',
        updated_by text NOT NULL DEFAULT '',
        updated_at timestamptz NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS gate_events (
        id serial PRIMARY KEY,
        machine text NOT NULL,
        action text NOT NULL,
        actor text NOT NULL,
        created_at timestamptz NOT NULL
    )
    """,
]


class LoginIn(BaseModel):
    username: str
    password: str


class JobIn(BaseModel):
    sheet: str
    machine: str
    cyan_mm: float
    magenta_mm: float


def current_user(credentials: HTTPAuthorizationCredentials | None = Depends(security)) -> dict:
    if credentials is None:
        raise HTTPException(status_code=401, detail="未登录")
    try:
        payload = jwt.decode(credentials.credentials, SECRET, algorithms=["HS256"])
    except JWTError as exc:
        raise HTTPException(status_code=401, detail="无效令牌") from exc
    if payload.get("sub") not in USERS:
        raise HTTPException(status_code=401, detail="无效令牌")
    return {"username": payload["sub"], "role": payload.get("role")}


def require_writer(user: dict = Depends(current_user)) -> dict:
    if user["role"] != "writer":
        raise HTTPException(status_code=403, detail="仅印刷员可送复核")
    return user


app = FastAPI(title="印刷套准复核台")


@app.on_event("startup")
def startup():
    with connect() as conn:
        for stmt in SCHEMA:
            conn.execute(stmt)
        now = datetime.now(timezone.utc)
        n = conn.execute("SELECT COUNT(*) AS n FROM jobs").fetchone()["n"]
        if n == 0:
            conn.execute(
                """INSERT INTO jobs (sheet, machine, cyan_mm, magenta_mm, status, verdict, reason, created_by, created_at)
                   VALUES
                   ('封面-01', '甲机', 0.05, -0.04, 'pending', '', '', 'printer', %s),
                   ('内页-09', '乙机', 0.40, 0.02, 'pending', '', '', 'printer', %s)""",
                (now, now),
            )
        g = conn.execute("SELECT COUNT(*) AS n FROM machine_gates").fetchone()["n"]
        if g == 0:
            conn.execute(
                """INSERT INTO machine_gates (machine, status, updated_by, updated_at)
                   VALUES ('甲机', 'open', 'system', %s), ('乙机', 'open', 'system', %s)""",
                (now, now),
            )
        conn.commit()


@app.get("/api/health")
def health():
    return {"status": "ok", "service": "print-register-review"}


@app.post("/api/auth/login")
def login(body: LoginIn):
    user = USERS.get(body.username.strip())
    if not user or not pwd.verify(body.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    exp = datetime.now(timezone.utc) + timedelta(hours=8)
    token = jwt.encode({"sub": body.username.strip(), "role": user["role"], "exp": exp}, SECRET, algorithm="HS256")
    return {"access_token": token, "username": body.username.strip(), "role": user["role"]}


@app.get("/api/jobs")
def list_jobs(_user: dict = Depends(current_user)):
    with connect() as conn:
        return conn.execute(
            "SELECT id, sheet, machine, cyan_mm, magenta_mm, status, verdict, reason, created_by FROM jobs ORDER BY id DESC"
        ).fetchall()


@app.post("/api/jobs", status_code=202)
def enqueue(body: JobIn, user: dict = Depends(require_writer)):
    machine = body.machine.strip()
    if not machine:
        raise HTTPException(status_code=422, detail="机台名不能为空")
    now = datetime.now(timezone.utc)
    with connect() as conn:
        conn.execute(
            """INSERT INTO machine_gates (machine, status, updated_by, updated_at)
               VALUES (%s, 'open', %s, %s)
               ON CONFLICT (machine) DO NOTHING""",
            (machine, user["username"], now),
        )
        row = conn.execute(
            """INSERT INTO jobs (sheet, machine, cyan_mm, magenta_mm, status, created_by, created_at)
               VALUES (%s, %s, %s, %s, 'pending', %s, %s)
               RETURNING id, sheet, machine, status, verdict""",
            (body.sheet.strip(), machine, body.cyan_mm, body.magenta_mm, user["username"], now),
        )
        conn.commit()
    return row


@app.get("/api/gates")
def list_gates(_user: dict = Depends(current_user)):
    with connect() as conn:
        return conn.execute(
            "SELECT machine, status, updated_by, updated_at FROM machine_gates ORDER BY machine"
        ).fetchall()


@app.get("/api/gate-events")
def list_gate_events(_user: dict = Depends(current_user)):
    with connect() as conn:
        return conn.execute(
            "SELECT id, machine, action, actor, created_at FROM gate_events ORDER BY id DESC LIMIT 200"
        ).fetchall()


def set_gate(machine: str, action: str, user: dict) -> dict:
    if user["role"] != "writer":
        raise HTTPException(status_code=403, detail="仅印刷员可操作闸门")
    machine = machine.strip()
    if not machine:
        raise HTTPException(status_code=422, detail="机台名不能为空")
    status = "paused" if action == "pause" else "open"
    now = datetime.now(timezone.utc)
    with connect() as conn:
        row = conn.execute(
            """INSERT INTO machine_gates (machine, status, updated_by, updated_at)
               VALUES (%s, %s, %s, %s)
               ON CONFLICT (machine) DO UPDATE
               SET status = EXCLUDED.status, updated_by = EXCLUDED.updated_by, updated_at = EXCLUDED.updated_at
               RETURNING machine, status, updated_by, updated_at""",
            (machine, status, user["username"], now),
        ).fetchone()
        conn.execute(
            "INSERT INTO gate_events (machine, action, actor, created_at) VALUES (%s, %s, %s, %s)",
            (machine, action, user["username"], now),
        )
        conn.commit()
    return row


@app.post("/api/gates/{machine}/pause")
def pause_gate(machine: str, user: dict = Depends(current_user)):
    return set_gate(machine, "pause", user)


@app.post("/api/gates/{machine}/resume")
def resume_gate(machine: str, user: dict = Depends(current_user)):
    return set_gate(machine, "resume", user)
