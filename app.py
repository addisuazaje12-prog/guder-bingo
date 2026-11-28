import os
import re
import json
import time
import hmac
import hashlib
import sqlite3
import logging
import threading
import random
from urllib.parse import parse_qsl
from contextlib import contextmanager
from flask import Flask, request, jsonify, send_from_directory, abort

# ============================================================
# GUDER BINGO BACKEND
# ============================================================

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get(
    "GUDER_DB_PATH",
    os.path.join(BASE_DIR, "guder_bingo.db")
)

BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
BOT_API_SECRET = os.environ.get("BOT_API_SECRET", "").strip()
ADMIN_PIN = os.environ.get("ADMIN_PIN", "").strip()
APP_ENV = os.environ.get("APP_ENV", "production").lower()

PAYMENT_PHONE = os.environ.get("TELEBIRR_NUMBER", "0913706670").strip()

MIN_DEPOSIT = float(os.environ.get("MIN_DEPOSIT_ETB", "1"))
MAX_DEPOSIT = float(os.environ.get("MAX_DEPOSIT_ETB", "100000"))

MIN_PLAY_BALANCE = 10.0
CARTELA_STAKE = 10.0
MAX_CARTELAS = 800

SELECTION_SECONDS = 30
PREPARATION_SECONDS = 5
WINNER_SECONDS = 5
NUMBER_CALL_INTERVAL = 3

BINGO_RANGES = {
    "B": range(1, 16),
    "I": range(16, 31),
    "N": range(31, 46),
    "G": range(46, 61),
    "O": range(61, 76)
}

app = Flask(__name__, static_folder=BASE_DIR)
app.config["MAX_CONTENT_LENGTH"] = 64 * 1024

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("guder-bingo")


# ============================================================
# DATABASE HELPERS
# ============================================================

def connect_db():
    conn = sqlite3.connect(DB_PATH, timeout=20)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=20000")
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


@contextmanager
def db_transaction():
    conn = connect_db()
    try:
        conn.execute("BEGIN IMMEDIATE")
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


@contextmanager
def db_read():
    conn = connect_db()
    try:
        yield conn
    finally:
        conn.close()


def init_db():
    with db_transaction() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id TEXT NOT NULL UNIQUE,
            first_name TEXT NOT NULL DEFAULT '',
            username TEXT NOT NULL DEFAULT '',
            phone_number TEXT NOT NULL DEFAULT '',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS game_rooms (
            room_id INTEGER PRIMARY KEY AUTOINCREMENT,
            status TEXT NOT NULL DEFAULT 'OPEN',
            selection_ends_at INTEGER,
            preparation_ends_at INTEGER,
            winner_user_id INTEGER,
            winner_cartela INTEGER,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            completed_at TEXT
        );

        CREATE TABLE IF NOT EXISTS ledger_transactions (
            txn_id TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL,
            amount REAL NOT NULL,
            txn_type TEXT NOT NULL,
            reference_id TEXT NOT NULL UNIQUE,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(user_id) REFERENCES users(user_id)
        );

        CREATE TABLE IF NOT EXISTS provisional_deposits (
            deposit_id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            amount REAL NOT NULL,
            payer_reference TEXT NOT NULL UNIQUE,
            receipt_text TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'PROVISIONAL',
            admin_note TEXT DEFAULT '',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            reviewed_at TEXT,
            FOREIGN KEY(user_id) REFERENCES users(user_id)
        );

        CREATE TABLE IF NOT EXISTS cartelas (
            cartela_id INTEGER PRIMARY KEY AUTOINCREMENT,
            room_id INTEGER NOT NULL,
            cartela_no INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            numbers_json TEXT NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(room_id, cartela_no),
            FOREIGN KEY(room_id) REFERENCES game_rooms(room_id),
            FOREIGN KEY(user_id) REFERENCES users(user_id)
        );

        CREATE TABLE IF NOT EXISTS called_numbers (
            call_id INTEGER PRIMARY KEY AUTOINCREMENT,
            room_id INTEGER NOT NULL,
            number INTEGER NOT NULL,
            called_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(room_id, number),
            FOREIGN KEY(room_id) REFERENCES game_rooms(room_id)
        );

        CREATE TABLE IF NOT EXISTS game_results (
            result_id INTEGER PRIMARY KEY AUTOINCREMENT,
            room_id INTEGER NOT NULL UNIQUE,
            user_id INTEGER NOT NULL,
            cartela_no INTEGER NOT NULL,
            winning_pattern TEXT NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(room_id) REFERENCES game_rooms(room_id),
            FOREIGN KEY(user_id) REFERENCES users(user_id)
        );
        """)

        room = conn.execute(
            "SELECT room_id FROM game_rooms ORDER BY room_id DESC LIMIT 1"
        ).fetchone()

        if not room:
            conn.execute(
                "INSERT INTO game_rooms(status, selection_ends_at) VALUES('OPEN', ?)",
                (int(time.time()) + SELECTION_SECONDS,)
            )


# ============================================================
# TELEGRAM WEB APP AUTHENTICATION
# ============================================================

def verify_telegram_init_data(init_data):
    if not BOT_TOKEN or not init_data:
        return None

    try:
        values = dict(parse_qsl(init_data, keep_blank_values=True))
        received_hash = values.pop("hash", None)

        if not received_hash:
            return None

        auth_date = int(values.get("auth_date", "0"))
        now = int(time.time())

        if auth_date <= 0 or now - auth_date > 86400 or auth_date - now > 60:
            return None

        check_string = "\n".join(
            f"{k}={v}" for k, v in sorted(values.items())
        )

        secret = hmac.new(
            b"WebAppData",
            BOT_TOKEN.encode(),
            hashlib.sha256
        ).digest()

        expected = hmac.new(
            secret,
            check_string.encode(),
            hashlib.sha256
        ).hexdigest()

        if not hmac.compare_digest(expected, received_hash):
            return None

        user = json.loads(values.get("user", "{}"))
        return user if user.get("id") else None

    except Exception:
        log.exception("Telegram authentication failed")
        return None


def authenticated_user():
    tg_user = verify_telegram_init_data(
        request.headers.get("X-Telegram-Init-Data", "")
    )

    if not tg_user and APP_ENV == "development":
        test_id = request.headers.get("X-Test-User", "").strip()

        if test_id.isdigit():
            tg_user = {
                "id": int(test_id),
                "first_name": "Test Player",
                "username": "test_player"
            }

    if not tg_user:
        return None

    telegram_id = str(tg_user["id"])

    with db_transaction() as conn:
        conn.execute("""
            INSERT INTO users(telegram_id, first_name, username)
            VALUES(?,?,?)
            ON CONFLICT(telegram_id) DO UPDATE SET
                first_name=excluded.first_name,
                username=excluded.username
        """, (
            telegram_id,
            str(tg_user.get("first_name", ""))[:100],
            str(tg_user.get("username", ""))[:100]
        ))

        row = conn.execute(
            "SELECT * FROM users WHERE telegram_id=?",
            (telegram_id,)
        ).fetchone()

        return dict(row)


def require_user():
    user = authenticated_user()

    if not user:
        return None, (
            jsonify(
                success=False,
                status="error",
                error="Telegram authentication failed."
            ),
            401
        )

    return user, None


# ============================================================
# WALLET AND CARTELA HELPERS
# ============================================================

def balance_for(conn, user_id):
    row = conn.execute("""
        SELECT COALESCE(SUM(amount),0) AS balance
        FROM ledger_transactions
        WHERE user_id=?
    """, (user_id,)).fetchone()

    return round(float(row["balance"]), 2)


def add_ledger_entry(conn, user_id, amount, txn_type, reference):
    conn.execute("""
        INSERT INTO ledger_transactions(
            txn_id,user_id,amount,txn_type,reference_id
        )
        VALUES(?,?,?,?,?)
    """, (
        f"{txn_type}_{reference}_{time.time_ns()}",
        user_id,
        float(amount),
        txn_type,
        f"{txn_type}_{reference}"
    ))


def generate_cartela(room_id, cartela_no):
    rng = random.Random(f"{room_id}-{cartela_no}-GUDER")

    columns = [
        sorted(rng.sample(list(BINGO_RANGES[letter]), 5))
        for letter in "BINGO"
    ]

    matrix = [
        [columns[col][row] for col in range(5)]
        for row in range(5)
    ]

    matrix[2][2] = "FREE"
    return matrix


def winning_patterns(matrix, called):
    called = set(int(n) for n in called)

    lines = [matrix[row] for row in range(5)]

    lines += [
        [matrix[row][col] for row in range(5)]
        for col in range(5)
    ]

    lines += [
        [matrix[i][i] for i in range(5)],
        [matrix[i][4-i] for i in range(5)]
    ]

    return [
        f"line_{i+1}"
        for i, line in enumerate(lines)
        if all(
            str(v).upper() == "FREE" or int(v) in called
            for v in line
        )
    ]


# ============================================================
# GAME ENGINE
# ============================================================

def create_next_room(conn):
    conn.execute("""
        INSERT INTO game_rooms(status, selection_ends_at)
        VALUES('OPEN',?)
    """, (int(time.time()) + SELECTION_SECONDS,))


def game_engine_once():
    now = int(time.time())

    with db_transaction() as conn:
        room = conn.execute("""
            SELECT *
            FROM game_rooms
            WHERE status IN (
                'OPEN','REGISTRATION','PREPARATION','PLAYING'
            )
            ORDER BY room_id DESC
            LIMIT 1
        """).fetchone()

        if not room:
            latest = conn.execute("""
                SELECT *
                FROM game_rooms
                ORDER BY room_id DESC
                LIMIT 1
            """).fetchone()

            if latest and latest["status"] == "COMPLETED":
                completed = conn.execute("""
                    SELECT strftime('%s', completed_at) AS t
                    FROM game_rooms
                    WHERE room_id=?
                """, (latest["room_id"],)).fetchone()

                if completed and completed["t"]:
                    if now - int(completed["t"]) >= WINNER_SECONDS:
                        create_next_room(conn)

            elif not latest or latest["status"] == "CANCELLED":
                create_next_room(conn)

            return

        rid = room["room_id"]
        status = room["status"]

        if status in ("OPEN", "REGISTRATION"):
            end = room["selection_ends_at"]

            if end is None:
                conn.execute("""
                    UPDATE game_rooms
                    SET selection_ends_at=?
                    WHERE room_id=?
                """, (now + SELECTION_SECONDS, rid))

            elif now >= int(end):
                conn.execute("""
                    UPDATE game_rooms
                    SET status='PREPARATION',
                        preparation_ends_at=?
                    WHERE room_id=?
                """, (now + PREPARATION_SECONDS, rid))

        elif status == "PREPARATION":
            if (
                room["preparation_ends_at"]
                and now >= int(room["preparation_ends_at"])
            ):
                conn.execute("""
                    UPDATE game_rooms
                    SET status='PLAYING'
                    WHERE room_id=?
                """, (rid,))

        elif status == "PLAYING":
            last = conn.execute("""
                SELECT strftime('%s',called_at) AS t
                FROM called_numbers
                WHERE room_id=?
                ORDER BY call_id DESC
                LIMIT 1
            """, (rid,)).fetchone()

            last_time = int(last["t"]) if last and last["t"] else 0

            if now - last_time >= NUMBER_CALL_INTERVAL:
                called = {
                    r["number"]
                    for r in conn.execute("""
                        SELECT number
                        FROM called_numbers
                        WHERE room_id=?
                    """, (rid,)).fetchall()
                }

                remaining = [
                    n for n in range(1, 76)
                    if n not in called
                ]

                if remaining:
                    conn.execute("""
                        INSERT INTO called_numbers(room_id,number)
                        VALUES(?,?)
                    """, (rid, random.choice(remaining)))
                else:
                    conn.execute("""
                        UPDATE game_rooms
                        SET status='COMPLETED',
                            completed_at=CURRENT_TIMESTAMP
                        WHERE room_id=?
                    """, (rid,))


_engine_started = False
_engine_lock = threading.Lock()


def start_game_engine():
    global _engine_started

    with _engine_lock:
        if _engine_started:
            return

        _engine_started = True

    def loop():
        while True:
            try:
                game_engine_once()
            except Exception:
                log.exception("Game engine error")

            time.sleep(2)

    threading.Thread(
        target=loop,
        daemon=True,
        name="guder-game-engine"
    ).start()


@app.before_request
def ensure_engine_started():
    start_game_engine()


# ============================================================
# WEB / AUDIO / CONFIG
# ============================================================

@app.get("/")
def home():
    return send_from_directory(
        BASE_DIR,
        "index.html",
        conditional=True
    )


@app.get("/health")
def health():
    return jsonify(
        status="ok",
        service="Guder Bingo API"
    )


@app.get("/bingo_amharic_audio/<path:filename>")
def amharic_audio(filename):
    directory = os.path.join(
        BASE_DIR,
        "bingo_amharic_audio"
    )

    if not os.path.isdir(directory):
        abort(404)

    response = send_from_directory(
        directory,
        filename,
        conditional=True
    )

    response.headers["Cache-Control"] = "public, max-age=3600"
    return response


@app.get("/audio/<path:filename>")
def audio_alias(filename):
    directory = os.path.join(
        BASE_DIR,
        "bingo_amharic_audio"
    )

    return send_from_directory(
        directory,
        filename,
        conditional=True
    )


@app.get("/api/config")
def config():
    return jsonify(
        success=True,
        status="success",
        telebirr_phone=PAYMENT_PHONE,
        min_play_balance=MIN_PLAY_BALANCE,
        max_cartela_no=MAX_CARTELAS,
        selection_seconds=SELECTION_SECONDS,
        preparation_seconds=PREPARATION_SECONDS,
        number_call_interval=NUMBER_CALL_INTERVAL,
        deposit_verification="PROVISIONAL_UNTIL_MANAGER_REVIEW",
        chapa_enabled=False
    )


# ============================================================
# WALLET API
# ============================================================

@app.get("/api/wallet")
def wallet():
    user, error = require_user()

    if error:
        return error

    with db_read() as conn:
        balance = balance_for(conn, user["user_id"])

        transactions = conn.execute("""
            SELECT *
            FROM ledger_transactions
            WHERE user_id=?
            ORDER BY created_at DESC
            LIMIT 50
        """, (user["user_id"],)).fetchall()

    return jsonify(
        success=True,
        status="success",
        balance=balance,
        transactions=[dict(x) for x in transactions]
    )


# ============================================================
# TELEBIRR RECEIPT PARSER AND BOT API
# ============================================================

def extract_receipt_details(receipt):
    amount_patterns = [
        r"(?:amount|paid|birr|ETB|total)\s*[:\-]?\s*([0-9,]+(?:\.[0-9]{1,2})?)",
        r"([0-9,]+(?:\.[0-9]{1,2})?)\s*(?:ETB|Birr)"
    ]

    amount_match = next(
        (
            m for p in amount_patterns
            if (m := re.search(p, receipt, re.I))
        ),
        None
    )

    reference_patterns = [
        r"(?:transaction\s*(?:id|no|number|reference)?|reference|txn\s*id|receipt\s*no)\s*[:#\-]?\s*([A-Z0-9\-]{5,50})",
        r"(?:የግብይት\s*(?:ቁጥር|መለያ)|የደረሰኝ\s*ቁጥር)\s*[:#\-]?\s*([A-Z0-9\-]{5,50})"
    ]

    reference_match = next(
        (
            m for p in reference_patterns
            if (m := re.search(p, receipt, re.I))
        ),
        None
    )

    if not amount_match or not reference_match:
        return None

    try:
        amount = float(
            amount_match.group(1).replace(",", "")
        )
    except ValueError:
        return None

    reference = reference_match.group(1).strip()

    if not (MIN_DEPOSIT <= amount <= MAX_DEPOSIT):
        return None

    return amount, reference


@app.post("/api/bot/deposit")
def bot_deposit():
    supplied = request.headers.get("X-Bot-Secret", "")

    if not BOT_API_SECRET or not hmac.compare_digest(
        supplied,
        BOT_API_SECRET
    ):
        return jsonify(
            success=False,
            error="Unauthorized bot request."
        ), 401

    data = request.get_json(silent=True) or {}

    telegram_id = str(data.get("user_id", "")).strip()
    receipt = str(data.get("receipt", "")).strip()

    if not telegram_id.isdigit():
        return jsonify(
            success=False,
            error="Invalid Telegram user."
        ), 400

    if len(receipt) < 10 or len(receipt) > 5000:
        return jsonify(
            success=False,
            error="Receipt text is incomplete."
        ), 400

    details = extract_receipt_details(receipt)

    if not details:
        return jsonify(
            success=False,
            error="Could not identify the payment amount and transaction reference. Please submit the complete receipt."
        ), 400

    amount, reference = details

    try:
        with db_transaction() as conn:
            conn.execute("""
                INSERT INTO users(
                    telegram_id,first_name,username
                )
                VALUES(?,?,?)
                ON CONFLICT(telegram_id) DO UPDATE SET
                    first_name=excluded.first_name,
                    username=excluded.username
            """, (
                telegram_id,
                str(data.get("first_name", ""))[:100],
                str(data.get("username", ""))[:100]
            ))

            user = conn.execute("""
                SELECT *
                FROM users
                WHERE telegram_id=?
            """, (telegram_id,)).fetchone()

            cur = conn.execute("""
                INSERT INTO provisional_deposits(
                    user_id,amount,payer_reference,receipt_text,status
                )
                VALUES(?,?,?,?,'PROVISIONAL')
            """, (
                user["user_id"],
                amount,
                reference,
                receipt
            ))

            deposit_id = cur.lastrowid

            # IMMEDIATE PROVISIONAL PLAYING CREDIT
            add_ledger_entry(
                conn,
                user["user_id"],
                amount,
                "PROVISIONAL_DEPOSIT",
                f"deposit_{deposit_id}"
            )

    except sqlite3.IntegrityError:
        return jsonify(
            success=False,
            error="This transaction reference has already been submitted."
        ), 409

    return jsonify(
        success=True,
        status="PROVISIONAL",
        amount=amount,
        reference=reference,
        deposit_id=deposit_id,
        message="Playing credit recorded immediately."
    )


@app.get("/api/deposits/<int:deposit_id>")
def deposit_status(deposit_id):
    user, error = require_user()

    if error:
        return error

    with db_read() as conn:
        row = conn.execute("""
            SELECT *
            FROM provisional_deposits
            WHERE deposit_id=? AND user_id=?
        """, (
            deposit_id,
            user["user_id"]
        )).fetchone()

    if not row:
        return jsonify(
            success=False,
            error="Deposit not found."
        ), 404

    return jsonify(
        success=True,
        status="success",
        deposit=dict(row)
    )


# ============================================================
# MANAGER APIs
# ============================================================

def manager_authorized():
    supplied = request.headers.get("X-Manager-PIN", "")

    return (
        bool(ADMIN_PIN)
        and hmac.compare_digest(supplied, ADMIN_PIN)
    )


def manager_guard():
    if not manager_authorized():
        return jsonify(
            success=False,
            status="error",
            error="Manager authentication failed."
        ), 401

    return None


@app.get("/api/manager/overview")
def manager_overview():
    denied = manager_guard()

    if denied:
        return denied

    with db_read() as conn:
        room = conn.execute("""
            SELECT *
            FROM game_rooms
            ORDER BY room_id DESC
            LIMIT 1
        """).fetchone()

        deposits = conn.execute("""
            SELECT d.*,u.telegram_id,u.first_name,u.username
            FROM provisional_deposits d
            JOIN users u ON u.user_id=d.user_id
            ORDER BY d.created_at DESC
            LIMIT 100
        """).fetchall()

        players = conn.execute("""
            SELECT COUNT(*) AS n FROM users
        """).fetchone()["n"]

        deposit_count = conn.execute("""
            SELECT COUNT(*) AS n FROM provisional_deposits
        """).fetchone()["n"]

    dep_list = []

    for row in deposits:
        d = dict(row)
        d["id"] = d["deposit_id"]
        d["reference"] = d["payer_reference"]
        dep_list.append(d)

    return jsonify(
        success=True,
        status="success",
        room_id=room["room_id"] if room else None,
        current_room=room["room_id"] if room else None,
        game_status=room["status"] if room else "WAITING",
        players=players,
        deposit_count=deposit_count,
        deposits=dep_list
    )


@app.post("/api/manager/deposit/<int:deposit_id>/verify")
def manager_verify_deposit(deposit_id):
    denied = manager_guard()

    if denied:
        return denied

    with db_transaction() as conn:
        deposit = conn.execute("""
            SELECT *
            FROM provisional_deposits
            WHERE deposit_id=?
        """, (deposit_id,)).fetchone()

        if not deposit:
            return jsonify(
                success=False,
                error="Deposit not found."
            ), 404

        if deposit["status"] != "PROVISIONAL":
            return jsonify(
                success=False,
                error="Deposit has already been reviewed."
            ), 409

        conn.execute("""
            UPDATE provisional_deposits
            SET status='VERIFIED',
                admin_note='Payment independently confirmed',
                reviewed_at=CURRENT_TIMESTAMP
            WHERE deposit_id=?
        """, (deposit_id,))

    return jsonify(
        success=True,
        message="Deposit verified successfully."
    )


@app.post("/api/manager/deposit/<int:deposit_id>/reject")
def manager_reject_deposit(deposit_id):
    denied = manager_guard()

    if denied:
        return denied

    with db_transaction() as conn:
        deposit = conn.execute("""
            SELECT *
            FROM provisional_deposits
            WHERE deposit_id=?
        """, (deposit_id,)).fetchone()

        if not deposit:
            return jsonify(
                success=False,
                error="Deposit not found."
            ), 404

        if deposit["status"] != "PROVISIONAL":
            return jsonify(
                success=False,
                error="Deposit has already been reviewed."
            ), 409

        add_ledger_entry(
            conn,
            deposit["user_id"],
            -float(deposit["amount"]),
            "DEPOSIT_REVERSAL",
            f"deposit_{deposit_id}"
        )

        conn.execute("""
            UPDATE provisional_deposits
            SET status='REJECTED',
                admin_note='Payment could not be verified',
                reviewed_at=CURRENT_TIMESTAMP
            WHERE deposit_id=?
        """, (deposit_id,))

    return jsonify(
        success=True,
        message="Deposit rejected and provisional credit reversed."
    )


# ============================================================
# GAME STATE API
# ============================================================

@app.get("/api/game/state")
def game_state():
    user = authenticated_user()

    with db_read() as conn:
        active = conn.execute("""
            SELECT *
            FROM game_rooms
            WHERE status IN (
                'OPEN','REGISTRATION','PREPARATION','PLAYING'
            )
            ORDER BY room_id DESC
            LIMIT 1
        """).fetchone()

        latest = active or conn.execute("""
            SELECT *
            FROM game_rooms
            ORDER BY room_id DESC
            LIMIT 1
        """).fetchone()

        if not latest:
            return jsonify(
                success=True,
                status="success",
                game_status="WAITING",
                room=None,
                selections=[],
                called_numbers=[],
                winners=[]
            )

        rid = latest["room_id"]

        selections = conn.execute("""
            SELECT c.cartela_no,c.numbers_json,
                   u.telegram_id,u.first_name,u.username
            FROM cartelas c
            JOIN users u ON u.user_id=c.user_id
            WHERE c.room_id=?
            ORDER BY c.cartela_no
        """, (rid,)).fetchall()

        calls = conn.execute("""
            SELECT number,called_at
            FROM called_numbers
            WHERE room_id=?
            ORDER BY call_id
        """, (rid,)).fetchall()

        winners = conn.execute("""
            SELECT gr.room_id,gr.cartela_no,gr.created_at,
                   u.first_name,u.username,u.telegram_id
            FROM game_results gr
            JOIN users u ON u.user_id=gr.user_id
            ORDER BY gr.created_at DESC
            LIMIT 5
        """).fetchall()

        balance = (
            balance_for(conn, user["user_id"])
            if user else 0
        )

        own = conn.execute("""
            SELECT cartela_no,numbers_json
            FROM cartelas
            WHERE room_id=? AND user_id=?
            ORDER BY cartela_id DESC
            LIMIT 1
        """, (
            rid,
            user["user_id"]
        )).fetchone() if user else None

    status_map = {
        "OPEN": "OPEN",
        "REGISTRATION": "OPEN",
        "PREPARATION": "PREPARING",
        "PLAYING": "RUNNING",
        "COMPLETED": "COMPLETED",
        "CANCELLED": "COMPLETED"
    }

    game_status = status_map.get(
        latest["status"],
        "WAITING"
    )

    now = int(time.time())
    remaining = 0

    if game_status == "OPEN" and latest["selection_ends_at"]:
        remaining = max(
            0,
            int(latest["selection_ends_at"]) - now
        )

    elif game_status == "PREPARING" and latest["preparation_ends_at"]:
        remaining = max(
            0,
            int(latest["preparation_ends_at"]) - now
        )

    elif game_status == "COMPLETED" and latest["completed_at"]:
        try:
            completed = int(time.mktime(
                time.strptime(
                    latest["completed_at"],
                    "%Y-%m-%d %H:%M:%S"
                )
            ))

            remaining = max(
                0,
                WINNER_SECONDS - (now - completed)
            )

        except ValueError:
            pass

    selection_data = [
        {
            "cartela_number": r["cartela_no"],
            "cartela_no": r["cartela_no"],
            "telegram_user_id": r["telegram_id"],
            "first_name": r["first_name"],
            "username": r["username"],
            "numbers": json.loads(r["numbers_json"])
        }
        for r in selections
    ]

    winner_data = [
        {
            "room_id": r["room_id"],
            "winner_name": (
                r["first_name"]
                or r["username"]
                or r["telegram_id"]
            ),
            "cartela_no": r["cartela_no"],
            "created_at": r["created_at"],
            "prize_amount": 0
        }
        for r in winners
    ]

    return jsonify(
        success=True,
        status="success",
        game_status=game_status,
        room_id=rid,
        room=dict(latest),
        remaining_seconds=remaining,
        selections=selection_data,
        called_numbers=[r["number"] for r in calls],
        winners=winner_data,
        balance=balance,
        cartela={
            "cartela_no": own["cartela_no"],
            "numbers_json": own["numbers_json"]
        } if own else None,
        can_play=bool(
            user and balance >= MIN_PLAY_BALANCE
        )
    )


# ============================================================
# CARTELA SELECTION
# ============================================================

@app.post("/api/cartela/select")
def select_cartela():
    user, error = require_user()

    if error:
        return error

    data = request.get_json(silent=True) or {}

    try:
        cartela_no = int(
            data.get(
                "cartela_number",
                data.get("cartela_no")
            )
        )
    except (ValueError, TypeError):
        return jsonify(
            success=False,
            error="Invalid cartela number."
        ), 400

    if not 1 <= cartela_no <= MAX_CARTELAS:
        return jsonify(
            success=False,
            error="Cartela number must be between 1 and 800."
        ), 400

    with db_transaction() as conn:
        balance = balance_for(
            conn,
            user["user_id"]
        )

        if balance < MIN_PLAY_BALANCE:
            return jsonify(
                success=False,
                status="insufficient_balance",
                error="Insufficient wallet balance.",
                message="Please deposit before selecting a cartela."
            ), 403

        room = conn.execute("""
            SELECT *
            FROM game_rooms
            WHERE status IN ('OPEN','REGISTRATION')
            ORDER BY room_id DESC
            LIMIT 1
        """).fetchone()

        if not room:
            return jsonify(
                success=False,
                error="Cartela selection is closed."
            ), 409

        if (
            room["selection_ends_at"]
            and int(time.time()) >= int(room["selection_ends_at"])
        ):
            return jsonify(
                success=False,
                error="The selection period has ended."
            ), 409

        matrix = generate_cartela(
            room["room_id"],
            cartela_no
        )

        try:
            conn.execute("""
                INSERT INTO cartelas(
                    room_id,cartela_no,user_id,numbers_json
                )
                VALUES(?,?,?,?)
            """, (
                room["room_id"],
                cartela_no,
                user["user_id"],
                json.dumps(matrix)
            ))

        except sqlite3.IntegrityError:
            return jsonify(
                success=False,
                error="This cartela is already selected."
            ), 409

        add_ledger_entry(
            conn,
            user["user_id"],
            -CARTELA_STAKE,
            "CARTELA_STAKE",
            f"room_{room['room_id']}_cartela_{cartela_no}"
        )

    return jsonify(
        success=True,
        status="success",
        message="Cartela selected successfully.",
        cartela_number=cartela_no,
        numbers=matrix,
        room_id=room["room_id"],
        selection_color="green"
    )


# ============================================================
# BINGO CLAIM
# ============================================================

@app.post("/api/game/claim")
def claim_bingo():
    user, error = require_user()

    if error:
        return error

    with db_transaction() as conn:
        room = conn.execute("""
            SELECT *
            FROM game_rooms
            WHERE status='PLAYING'
            ORDER BY room_id DESC
            LIMIT 1
        """).fetchone()

        if not room:
            return jsonify(
                success=False,
                error="There is no active game."
            ), 409

        card = conn.execute("""
            SELECT *
            FROM cartelas
            WHERE room_id=? AND user_id=?
            ORDER BY cartela_id DESC
            LIMIT 1
        """, (
            room["room_id"],
            user["user_id"]
        )).fetchone()

        if not card:
            return jsonify(
                success=False,
                error="You have not selected a cartela."
            ), 403

        matrix = json.loads(card["numbers_json"])

        called = {
            r["number"]
            for r in conn.execute("""
                SELECT number
                FROM called_numbers
                WHERE room_id=?
            """, (room["room_id"],)).fetchall()
        }

        patterns = winning_patterns(matrix, called)

        if not patterns:
            return jsonify(
                success=False,
                error="Bingo could not be verified."
            ), 400

        changed = conn.execute("""
            UPDATE game_rooms
            SET status='COMPLETED',
                winner_user_id=?,
                winner_cartela=?,
                completed_at=CURRENT_TIMESTAMP
            WHERE room_id=? AND status='PLAYING'
        """, (
            user["user_id"],
            card["cartela_no"],
            room["room_id"]
        )).rowcount

        if changed != 1:
            return jsonify(
                success=False,
                error="Another winning claim has already been processed."
            ), 409

        conn.execute("""
            INSERT INTO game_results(
                room_id,user_id,cartela_no,winning_pattern
            )
            VALUES(?,?,?,?)
        """, (
            room["room_id"],
            user["user_id"],
            card["cartela_no"],
            ",".join(patterns)
        ))

    return jsonify(
        success=True,
        status="winner",
        message="Bingo claim verified.",
        winner=user["first_name"] or user["username"],
        cartela_no=card["cartela_no"],
        winning_patterns=patterns,
        celebration_seconds=WINNER_SECONDS
    )


# ============================================================
# HISTORY
# ============================================================

@app.get("/api/game/history")
def game_history():
    with db_read() as conn:
        rows = conn.execute("""
            SELECT gr.room_id,gr.cartela_no,
                   gr.winning_pattern,gr.created_at,
                   u.first_name,u.username,
                   u.phone_number,u.telegram_id
            FROM game_results gr
            JOIN users u ON u.user_id=gr.user_id
            ORDER BY gr.created_at DESC
            LIMIT 100
        """).fetchall()

    winners = [
        {
            "room_id": r["room_id"],
            "winner_name": (
                r["first_name"]
                or r["username"]
                or r["telegram_id"]
            ),
            "phone": r["phone_number"],
            "cartela_no": r["cartela_no"],
            "prize_amount": 0,
            "created_at": r["created_at"]
        }
        for r in rows
    ]

    return jsonify(
        success=True,
        status="success",
        winners=winners,
        history=winners
    )


# ============================================================
# ERRORS / INITIALIZATION
# ============================================================

@app.errorhandler(413)
def request_too_large(error):
    return jsonify(
        success=False,
        error="Request is too large."
    ), 413


@app.errorhandler(500)
def internal_error(error):
    log.exception("Internal server error")

    return jsonify(
        success=False,
        error="Internal server error."
    ), 500


init_db()


if __name__ == "__main__":
    start_game_engine()

    port = int(os.environ.get("PORT", "5000"))

    app.run(
        host="0.0.0.0",
        port=port,
        debug=(APP_ENV == "development")
    )
