# sqlite, stdlib only. invoice numbers and what stage each checkout/refund is at
# exists because of the kmg wifi problem, bill should not vanish or get created twice
# fine for one process here, 7 stores will need a shared allocator

import json
import sqlite3
from datetime import datetime, timezone


def now_utc():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Store:
    def __init__(self, path):
        self.path = path
        self._init()

    def connect(self):
        conn = sqlite3.connect(self.path, timeout=15, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 15000")
        return conn

    def _init(self):
        with self.connect() as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS counters (
                store_code TEXT NOT NULL,
                series TEXT NOT NULL,
                next_no INTEGER NOT NULL,
                PRIMARY KEY(store_code, series)
            );
            CREATE TABLE IF NOT EXISTS checkouts (
                checkout_key TEXT PRIMARY KEY,
                store_code TEXT NOT NULL,
                invoice_no TEXT NOT NULL UNIQUE,
                status TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                charge_id TEXT,
                order_id INTEGER,
                error TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS refund_intents (
                refund_key TEXT PRIMARY KEY,
                order_id INTEGER NOT NULL,
                invoice_no TEXT NOT NULL,
                credit_note_no TEXT NOT NULL UNIQUE,
                status TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                terminal_refund_id TEXT,
                amount_paise INTEGER NOT NULL,
                error TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            """)

    def get_checkout(self, key):
        with self.connect() as db:
            row = db.execute("SELECT * FROM checkouts WHERE checkout_key=?", (key,)).fetchone()
            return dict(row) if row else None

    def begin_checkout(self, key, store_code, invoice_prefix, remote_max, payload):
        db = self.connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute("SELECT * FROM checkouts WHERE checkout_key=?", (key,)).fetchone()
            if existing:
                row = dict(existing)
                db.execute("COMMIT")
                return row, False
            baseline = int(remote_max) + 1
            counter = db.execute("SELECT next_no FROM counters WHERE store_code=? AND series='invoice'", (store_code,)).fetchone()
            if counter:
                number = max(int(counter[0]), baseline)
                db.execute("UPDATE counters SET next_no=? WHERE store_code=? AND series='invoice'", (number + 1, store_code))
            else:
                number = baseline
                db.execute("INSERT INTO counters(store_code,series,next_no) VALUES(?,?,?)", (store_code, "invoice", number + 1))
            invoice = invoice_prefix + str(number).zfill(5)
            stamp = now_utc()
            db.execute("INSERT INTO checkouts(checkout_key,store_code,invoice_no,status,payload_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                       (key, store_code, invoice, "prepared", json.dumps(payload, separators=(",", ":")), stamp, stamp))
            row = db.execute("SELECT * FROM checkouts WHERE checkout_key=?", (key,)).fetchone()
            db.execute("COMMIT")
            return dict(row), True
        except Exception:
            db.execute("ROLLBACK")
            raise
        finally:
            db.close()

    def update_checkout(self, key, status, charge_id=None, order_id=None, error=None):
        with self.connect() as db:
            db.execute("UPDATE checkouts SET status=?, charge_id=COALESCE(?,charge_id), order_id=COALESCE(?,order_id), error=?, updated_at=? WHERE checkout_key=?",
                       (status, charge_id, order_id, error, now_utc(), key))
        return self.get_checkout(key)

    def reserve_credit_note(self, store_code, prefix, remote_max, refund_key, order_id, invoice_no, payload, amount_paise):
        db = self.connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute("SELECT * FROM refund_intents WHERE refund_key=?", (refund_key,)).fetchone()
            if existing:
                row = dict(existing)
                db.execute("COMMIT")
                return row, False
            baseline = int(remote_max) + 1
            counter = db.execute("SELECT next_no FROM counters WHERE store_code=? AND series='credit_note'", (store_code,)).fetchone()
            number = max(int(counter[0]), baseline) if counter else baseline
            if counter:
                db.execute("UPDATE counters SET next_no=? WHERE store_code=? AND series='credit_note'", (number + 1, store_code))
            else:
                db.execute("INSERT INTO counters(store_code,series,next_no) VALUES(?,?,?)", (store_code, "credit_note", number + 1))
            credit = prefix + "CN/" + str(number).zfill(5)
            stamp = now_utc()
            db.execute("INSERT INTO refund_intents(refund_key,order_id,invoice_no,credit_note_no,status,payload_json,amount_paise,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
                       (refund_key, int(order_id), invoice_no, credit, "prepared", json.dumps(payload, separators=(",", ":")), int(amount_paise), stamp, stamp))
            row = db.execute("SELECT * FROM refund_intents WHERE refund_key=?", (refund_key,)).fetchone()
            db.execute("COMMIT")
            return dict(row), True
        except Exception:
            db.execute("ROLLBACK")
            raise
        finally:
            db.close()

    def get_refund(self, key):
        with self.connect() as db:
            row = db.execute("SELECT * FROM refund_intents WHERE refund_key=?", (key,)).fetchone()
            return dict(row) if row else None

    def update_refund(self, key, status, terminal_refund_id=None, error=None):
        with self.connect() as db:
            db.execute("UPDATE refund_intents SET status=?, terminal_refund_id=COALESCE(?,terminal_refund_id), error=?, updated_at=? WHERE refund_key=?",
                       (status, terminal_refund_id, error, now_utc(), key))
        return self.get_refund(key)

    def claim_checkout(self,key,expected,new_status):
        with self.connect() as db:
            cur=db.execute("UPDATE checkouts SET status=?,error=NULL,updated_at=? WHERE checkout_key=? AND status=?",(new_status,now_utc(),key,expected))
            return cur.rowcount==1

    def claim_refund(self,key,expected,new_status):
        with self.connect() as db:
            cur=db.execute("UPDATE refund_intents SET status=?,error=NULL,updated_at=? WHERE refund_key=? AND status=?",(new_status,now_utc(),key,expected))
            return cur.rowcount==1
