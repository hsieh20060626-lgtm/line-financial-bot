"""PostgreSQL-backed LINE customer registry and release delivery ledger."""
import logging
import os
import threading

logger = logging.getLogger(__name__)
_schema_lock = threading.Lock()
_schema_ready = False


def database_configured():
    return bool(os.getenv("DATABASE_URL", "").strip())


def _connect():
    if not database_configured():
        raise RuntimeError("尚未設定 DATABASE_URL")
    import psycopg
    return psycopg.connect(os.environ["DATABASE_URL"], connect_timeout=8)


def ensure_schema():
    global _schema_ready
    if _schema_ready:
        return
    with _schema_lock:
        if _schema_ready:
            return
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS line_customers (
                        user_id TEXT PRIMARY KEY,
                        first_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        last_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        status TEXT NOT NULL DEFAULT 'active'
                            CHECK (status IN ('active', 'inactive')),
                        followed_at TIMESTAMPTZ,
                        unfollowed_at TIMESTAMPTZ
                    )
                """)
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS release_deliveries (
                        version TEXT NOT NULL,
                        user_id TEXT NOT NULL REFERENCES line_customers(user_id),
                        status TEXT NOT NULL CHECK (status IN ('sent', 'failed')),
                        sent_at TIMESTAMPTZ,
                        error TEXT,
                        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        PRIMARY KEY (version, user_id)
                    )
                """)
        _schema_ready = True


def register_customer(user_id, source="message"):
    if not user_id or not database_configured():
        return False
    try:
        ensure_schema()
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO line_customers
                        (user_id, first_seen_at, last_seen_at, status, followed_at)
                    VALUES (%s, NOW(), NOW(), 'active',
                            CASE WHEN %s = 'follow' THEN NOW() ELSE NULL END)
                    ON CONFLICT (user_id) DO UPDATE SET
                        last_seen_at = NOW(), status = 'active', unfollowed_at = NULL,
                        followed_at = CASE WHEN %s = 'follow'
                            THEN NOW() ELSE line_customers.followed_at END
                """, (user_id, source, source))
        return True
    except Exception:
        logger.exception("保存 LINE 使用者名單失敗")
        return False


def mark_unfollowed(user_id):
    if not user_id or not database_configured():
        return False
    try:
        ensure_schema()
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO line_customers
                        (user_id, first_seen_at, last_seen_at, status, unfollowed_at)
                    VALUES (%s, NOW(), NOW(), 'inactive', NOW())
                    ON CONFLICT (user_id) DO UPDATE SET
                        last_seen_at = NOW(), status = 'inactive', unfollowed_at = NOW()
                """, (user_id,))
        return True
    except Exception:
        logger.exception("更新 LINE 使用者取消好友狀態失敗")
        return False


def pending_release_recipients(version):
    ensure_schema()
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT c.user_id
                FROM line_customers AS c
                LEFT JOIN release_deliveries AS d
                  ON d.user_id = c.user_id AND d.version = %s
                WHERE c.status = 'active'
                  AND (d.status IS NULL OR d.status <> 'sent')
                ORDER BY c.first_seen_at
            """, (version,))
            return [row[0] for row in cur.fetchall()]


def mark_release_delivered(version, user_ids, error=None):
    if not user_ids:
        return
    ensure_schema()
    status = "failed" if error else "sent"
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.executemany("""
                INSERT INTO release_deliveries
                    (version, user_id, status, sent_at, error, updated_at)
                VALUES (%s, %s, %s, CASE WHEN %s = 'sent' THEN NOW() ELSE NULL END,
                        %s, NOW())
                ON CONFLICT (version, user_id) DO UPDATE SET
                    status = EXCLUDED.status, sent_at = EXCLUDED.sent_at,
                    error = EXCLUDED.error, updated_at = NOW()
            """, [(version, uid, status, status, error[:500] if error else None)
                  for uid in user_ids])
