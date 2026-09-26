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
        paused boolean NOT NULL DEFAULT false,
        updated_by text NOT NULL DEFAULT '',
        updated_at timestamptz
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
        raise HTTPException(status_code=403, detail="仅印刷员可操作")
    return user


app = FastAPI(title="印刷套准复核台")


@app.on_event("startup")
def startup():
    with connect() as conn:
        for statement in SCHEMA:
            conn.execute(statement)
        n = conn.execute("SELECT COUNT(*) AS n FROM machine_gates").fetchone()["n"]
        if n == 0:
            conn.execute(
                "INSERT INTO machine_gates (machine) VALUES ('甲机'), ('乙机')"
            )
        n = conn.execute("SELECT COUNT(*) AS n FROM jobs").fetchone()["n"]
        if n == 0:
            now = datetime.now(timezone.utc)
            conn.execute(
                """INSERT INTO jobs (sheet, machine, cyan_mm, magenta_mm, status, verdict, reason, created_by, created_at)
                   VALUES
                   ('封面-01', '甲机', 0.05, -0.04, 'pending', '', '', 'printer', %s),
                   ('内页-09', '乙机', 0.40, 0.02, 'pending', '', '', 'printer', %s)""",
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
    sheet = body.sheet.strip()
    machine = body.machine.strip()
    if not sheet or not machine:
        raise HTTPException(status_code=422, detail="印张与机台不能为空")
    with connect() as conn:
        conn.execute(
            "INSERT INTO machine_gates (machine) VALUES (%s) ON CONFLICT (machine) DO NOTHING",
            (machine,),
        )
        row = conn.execute(
            """INSERT INTO jobs (sheet, machine, cyan_mm, magenta_mm, status, created_by, created_at)
               VALUES (%s, %s, %s, %s, 'pending', %s, %s)
               RETURNING id, sheet, machine, status, verdict""",
            (sheet, machine, body.cyan_mm, body.magenta_mm, user["username"], datetime.now(timezone.utc)),
        ).fetchone()
        conn.commit()
    return row


@app.get("/api/machines")
def list_machines(_user: dict = Depends(current_user)):
    with connect() as conn:
        return conn.execute(
            """SELECT g.machine, g.paused, g.updated_by, g.updated_at,
                      COALESCE(p.pending_count, 0) AS pending_count
               FROM machine_gates g
               LEFT JOIN (
                 SELECT machine, COUNT(*) AS pending_count
                 FROM jobs
                 WHERE status = 'pending'
                 GROUP BY machine
               ) p ON p.machine = g.machine
               ORDER BY g.machine"""
        ).fetchall()


def set_gate(machine: str, paused: bool, action: str, user: dict) -> dict:
    machine = machine.strip()
    if not machine:
        raise HTTPException(status_code=422, detail="机台名不能为空")
    now = datetime.now(timezone.utc)
    with connect() as conn:
        row = conn.execute(
            """INSERT INTO machine_gates (machine, paused, updated_by, updated_at)
               VALUES (%s, %s, %s, %s)
               ON CONFLICT (machine) DO UPDATE
               SET paused = EXCLUDED.paused,
                   updated_by = EXCLUDED.updated_by,
                   updated_at = EXCLUDED.updated_at
               RETURNING machine, paused, updated_by, updated_at""",
            (machine, paused, user["username"], now),
        ).fetchone()
        conn.execute(
            "INSERT INTO gate_events (machine, action, actor, created_at) VALUES (%s, %s, %s, %s)",
            (machine, action, user["username"], now),
        )
        conn.commit()
    return row


@app.post("/api/machines/{machine}/pause")
def pause_machine(machine: str, user: dict = Depends(require_writer)):
    return set_gate(machine, True, "pause", user)


@app.post("/api/machines/{machine}/resume")
def resume_machine(machine: str, user: dict = Depends(require_writer)):
    return set_gate(machine, False, "resume", user)


@app.get("/api/gate-events")
def list_gate_events(_user: dict = Depends(current_user)):
    with connect() as conn:
        return conn.execute(
            "SELECT id, machine, action, actor, created_at FROM gate_events ORDER BY id DESC LIMIT 100"
        ).fetchall()
