import os
import time

import psycopg
from psycopg.rows import dict_row

from rules import judge

DSN = os.environ["DATABASE_URL"]


def connect():
    last = None
    for _ in range(40):
        try:
            return psycopg.connect(DSN, row_factory=dict_row)
        except psycopg.OperationalError as exc:
            last = exc
            time.sleep(1)
    raise last


def ensure():
    with connect() as conn:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS jobs (
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
            )"""
        )
        conn.execute("ALTER TABLE jobs ADD COLUMN IF NOT EXISTS machine text NOT NULL DEFAULT ''")
        conn.execute(
            """CREATE TABLE IF NOT EXISTS machine_gates (
                machine text PRIMARY KEY,
                status text NOT NULL DEFAULT 'open',
                updated_by text NOT NULL DEFAULT '',
                updated_at timestamptz NOT NULL
            )"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS gate_events (
                id serial PRIMARY KEY,
                machine text NOT NULL,
                action text NOT NULL,
                actor text NOT NULL,
                created_at timestamptz NOT NULL
            )"""
        )
        conn.commit()


def claim_once(conn):
    row = conn.execute(
        """WITH picked AS (
             SELECT j.id FROM jobs j
             WHERE j.status = 'pending'
               AND NOT EXISTS (
                 SELECT 1 FROM machine_gates g
                 WHERE g.machine = j.machine AND g.status = 'paused'
               )
             ORDER BY j.id
             FOR UPDATE SKIP LOCKED
             LIMIT 1
           )
           UPDATE jobs SET status = 'running'
           FROM picked
           WHERE jobs.id = picked.id
           RETURNING jobs.id, jobs.cyan_mm, jobs.magenta_mm"""
    ).fetchone()
    return row


def main():
    ensure()
    while True:
        with connect() as conn:
            row = claim_once(conn)
            if row is None:
                conn.commit()
            else:
                verdict, reason = judge(row["cyan_mm"], row["magenta_mm"])
                conn.execute(
                    "UPDATE jobs SET status = 'done', verdict = %s, reason = %s WHERE id = %s",
                    (verdict, reason, row["id"]),
                )
                conn.commit()
        if row is None:
            time.sleep(0.4)


if __name__ == "__main__":
    main()
