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
from flask_cors import CORS

# ============================================================
# GUDER BINGO - BACKEND
# Compatible with the existing index.html and Telegram bot APIs
# ============================================================

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("GUDER_DB_PATH", os.path.join(BASE_DIR, "guder_bingo.db"))

BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
BOT_API_SECRET = os.environ.get("BOT_API_SECRET", "").strip()
# Set ADMIN_PIN in EthioDeploy. The fallback preserves the requested test PIN.
ADMIN_PIN = os.environ.get("ADMIN_PIN", "191919").strip()
APP_ENV = os.environ.get("APP_ENV", "production").lower()

PAYMENT_PHONE = os.environ.get("TELEBIRR_NUMBER", "0913706670").strip()
MIN_DEPOSIT = float(os.environ.get("MIN_DEPOSIT_ETB", "1"))
MAX_DEPOSIT = float(os.environ.get("MAX_DEPOSIT_ETB", "100000"))

CARTELA_STAKE = float(os.environ.get("CARTELA_STAKE_ETB", "10"))
MIN_PLAY_BALANCE = float(os.environ.get("MIN_PLAY_BALANCE_ETB", str(CARTELA_STAKE)))
MAX_CARTELAS = 800

SELECTION_SECONDS = 30
PREPARATION_SECONDS = 5
WINNER_SECONDS = 5
NUMBER_CALL_INTERVAL = 3

# The whole collected stake is recorded as the session prize pool.
# This is a record only; the app does not automatically send money.
PRIZE_RATE = float(os.environ.get("PRIZE_RATE", "1.0"))

BINGO_RANGES = {
    "B": range(1, 16),
    "I": range(16, 31),
    "N": range(31, 46),
    "G": range(46, 61),
    "O": range(61, 76),
}

app = Flask(__name__, static_folder=BASE_DIR)
app.config["MAX_CONTENT_LENGTH"] = 64 * 1024
CORS(app, resources={r"/api/*": {"origins": "*"}})

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("guder-bingo")


# ============================================================
# DATABASE
# ============================================================

def connect_db():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=30000")
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


def table_columns(conn, table):
    return {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}


def add_column_if_missing(conn, table, column, definition):
    if column not in table_columns(conn, table):
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def init_db():
    # Use execute statements (rather than executescript inside a transaction)
    # so existing database contents are preserved.
    with db_transaction() as conn:
        statements = [
            """CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id TEXT NOT NULL UNIQUE,
                first_name TEXT NOT NULL DEFAULT '',
                username TEXT NOT NULL DEFAULT '',
                phone_number TEXT NOT NULL DEFAULT '',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )""",
            """CREATE TABLE IF NOT EXISTS game_rooms (
                room_id INTEGER PRIMARY KEY AUTOINCREMENT,
                status TEXT NOT NULL DEFAULT 'OPEN',
                selection_ends_at INTEGER,
                preparation_ends_at INTEGER,
                winner_user_id INTEGER,
                winner_cartela INTEGER,
                prize_amount REAL NOT NULL DEFAULT 0,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                completed_at TEXT
            )""",
            """CREATE TABLE IF NOT EXISTS ledger_transactions (
                txn_id TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                amount REAL NOT NULL,
                txn_type TEXT NOT NULL,
                reference_id TEXT NOT NULL UNIQUE,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(user_id) REFERENCES users(user_id)
            )""",
            """CREATE TABLE IF NOT EXISTS provisional_deposits (
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
            )""",
            """CREATE TABLE IF NOT EXISTS cartelas (
                cartela_id INTEGER PRIMARY KEY AUTOINCREMENT,
                room_id INTEGER NOT NULL,
                cartela_no INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                numbers_json TEXT NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(room_id, cartela_no),
                FOREIGN KEY(room_id) REFERENCES game_rooms(room_id),
                FOREIGN KEY(user_id) REFERENCES users(user_id)
            )""",
            """CREATE TABLE IF NOT EXISTS called_numbers (
                call_id INTEGER PRIMARY KEY AUTOINCREMENT,
                room_id INTEGER NOT NULL,
                number INTEGER NOT NULL,
                called_at TEXT DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(room_id, number),
                FOREIGN KEY(room_id) REFERENCES game_rooms(room_id)
            )""",
            """CREATE TABLE IF NOT EXISTS game_results (
                result_id INTEGER PRIMARY KEY AUTOINCREMENT,
                room_id INTEGER NOT NULL UNIQUE,
                user_id INTEGER NOT NULL,
                cartela_no INTEGER NOT NULL,
                winning_pattern TEXT NOT NULL,
                prize_amount REAL NOT NULL DEFAULT 0,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(room_id) REFERENCES game_rooms(room_id),
                FOREIGN KEY(user_id) REFERENCES users(user_id)
            )""",
        ]
        for sql in statements:
            conn.execute(sql)

        # Safe schema upgrades for databases created by earlier app.py versions.
        add_column_if_missing(conn, "game_rooms", "prize_amount", "REAL NOT NULL DEFAULT 0")
        add_column_if_missing(conn, "game_results", "prize_amount", "REAL NOT NULL DEFAULT 0")
        add_column_if_missing(conn, "users", "phone_number", "TEXT NOT NULL DEFAULT ''")

        latest = conn.execute(
            "SELECT room_id, status FROM game_rooms ORDER BY room_id DESC LIMIT 1"
        ).fetchone()
        if not latest:
            conn.execute(
                "INSERT INTO game_rooms(status, selection_ends_at) VALUES('OPEN', ?)",
                (int(time.time()) + SELECTION_SECONDS,),
            )


# ============================================================
# AUTHENTICATION
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

        check_string = "\n".join(f"{key}={value}" for key, value in sorted(values.items()))
        secret = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
        expected = hmac.new(secret, check_string.encode(), hashlib.sha256).hexdigest()

        if not hmac.compare_digest(expected, received_hash):
            return None

        tg_user = json.loads(values.get("user", "{}"))
        return tg_user if tg_user.get("id") else None
    except Exception:
        log.exception("Telegram authentication failed")
        return None


def authenticated_user():
    tg_user = verify_telegram_init_data(
        request.headers.get("X-Telegram-Init-Data", "")
    )

    # Test-only authentication. Never enable APP_ENV=development on a public site.
    if not tg_user and APP_ENV == "development":
        test_id = request.headers.get("X-Test-User", "").strip()
        if test_id.isdigit():
            tg_user = {
                "id": int(test_id),
                "first_name": "Test Player",
                "username": "test_player",
            }

    if not tg_user:
        return None

    telegram_id = str(tg_user["id"])
    with db_transaction() as conn:
        conn.execute(
            """INSERT INTO users(telegram_id, first_name, username)
               VALUES(?,?,?)
               ON CONFLICT(telegram_id) DO UPDATE SET
                 first_name=excluded.first_name,
                 username=excluded.username""",
            (
                telegram_id,
                str(tg_user.get("first_name", ""))[:100],
                str(tg_user.get("username", ""))[:100],
            ),
        )
        row = conn.execute(
            "SELECT * FROM users WHERE telegram_id=?", (telegram_id,)
        ).fetchone()
        return dict(row)


def require_user():
    user = authenticated_user()
    if not user:
        return None, (
            jsonify(
                success=False,
                status="error",
                error="Telegram authentication failed. Open this game from the Telegram bot.",
                error_am="የቴሌግራም ማረጋገጫ አልተሳካም። ጨዋታውን ከቴሌግራም ቦት ይክፈቱ።",
            ),
            401,
        )
    return user, None


# ============================================================
# WALLET AND GAME HELPERS
# ============================================================

def balance_for(conn, user_id):
    row = conn.execute(
        "SELECT COALESCE(SUM(amount),0) AS balance FROM ledger_transactions WHERE user_id=?",
        (user_id,),
    ).fetchone()
    return round(float(row["balance"]), 2)


def add_ledger_entry(conn, user_id, amount, txn_type, reference):
    reference_id = f"{txn_type}_{reference}"
    conn.execute(
        """INSERT INTO ledger_transactions(txn_id,user_id,amount,txn_type,reference_id)
           VALUES(?,?,?,?,?)""",
        (
            f"{txn_type}_{reference}_{time.time_ns()}",
            user_id,
            round(float(amount), 2),
            txn_type,
            reference_id,
        ),
    )


def generate_cartela(room_id, cartela_no):
    rng = random.Random(f"{room_id}-{cartela_no}-GUDER")
    columns = [
        sorted(rng.sample(list(BINGO_RANGES[letter]), 5))
        for letter in "BINGO"
    ]
    matrix = [[columns[col][row] for col in range(5)] for row in range(5)]
    matrix[2][2] = "FREE"
    return matrix


def winning_patterns(matrix, called):
    called = {int(n) for n in called}
    lines = [matrix[row] for row in range(5)]
    lines += [[matrix[row][col] for row in range(5)] for col in range(5)]
    lines += [
        [matrix[i][i] for i in range(5)],
        [matrix[i][4 - i] for i in range(5)],
    ]

    patterns = []
    for i, line in enumerate(lines):
        if all(
            str(value).upper() == "FREE" or int(value) in called
            for value in line
        ):
            patterns.append(f"line_{i + 1}")
    return patterns


def create_next_room(conn):
    conn.execute(
        "INSERT INTO game_rooms(status,selection_ends_at) VALUES('OPEN',?)",
        (int(time.time()) + SELECTION_SECONDS,),
    )


# ============================================================
# GAME ENGINE
# ============================================================

def game_engine_once():
    now = int(time.time())

    with db_transaction() as conn:
        room = conn.execute(
            """SELECT * FROM game_rooms
               WHERE status IN ('OPEN','REGISTRATION','PREPARATION','PLAYING')
               ORDER BY room_id DESC LIMIT 1"""
        ).fetchone()

        if not room:
            latest = conn.execute(
                "SELECT * FROM game_rooms ORDER BY room_id DESC LIMIT 1"
            ).fetchone()

            if not latest or latest["status"] == "CANCELLED":
                create_next_room(conn)
                return

            if latest["status"] == "COMPLETED":
                completed = conn.execute(
                    "SELECT strftime('%s',completed_at) AS t FROM game_rooms WHERE room_id=?",
                    (latest["room_id"],),
                ).fetchone()
                if completed and completed["t"] and now - int(completed["t"]) >= WINNER_SECONDS:
                    create_next_room(conn)
            return

        rid = room["room_id"]
        status = room["status"]

        if status in ("OPEN", "REGISTRATION"):
            end = room["selection_ends_at"]
            if not end:
                conn.execute(
                    "UPDATE game_rooms SET selection_ends_at=? WHERE room_id=?",
                    (now + SELECTION_SECONDS, rid),
                )
            elif now >= int(end):
                count = conn.execute(
                    "SELECT COUNT(*) AS n FROM cartelas WHERE room_id=?", (rid,)
                ).fetchone()["n"]
                if count == 0:
                    conn.execute(
                        "UPDATE game_rooms SET status='CANCELLED',completed_at=CURRENT_TIMESTAMP WHERE room_id=?",
                        (rid,),
                    )
                else:
                    conn.execute(
                        "UPDATE game_rooms SET status='PREPARATION',preparation_ends_at=? WHERE room_id=?",
                        (now + PREPARATION_SECONDS, rid),
                    )

        elif status == "PREPARATION":
            if room["preparation_ends_at"] and now >= int(room["preparation_ends_at"]):
                conn.execute("UPDATE game_rooms SET status='PLAYING' WHERE room_id=?", (rid,))

        elif status == "PLAYING":
            last = conn.execute(
                "SELECT strftime('%s',called_at) AS t FROM called_numbers WHERE room_id=? ORDER BY call_id DESC LIMIT 1",
                (rid,),
            ).fetchone()
            last_time = int(last["t"]) if last and last["t"] else 0

            if now - last_time >= NUMBER_CALL_INTERVAL:
                called = {
                    row["number"]
                    for row in conn.execute(
                        "SELECT number FROM called_numbers WHERE room_id=?", (rid,)
                    ).fetchall()
                }
                remaining = [n for n in range(1, 76) if n not in called]
                if remaining:
                    conn.execute(
                        "INSERT INTO called_numbers(room_id,number) VALUES(?,?)",
                        (rid, random.choice(remaining)),
                    )
                else:
                    conn.execute(
                        "UPDATE game_rooms SET status='COMPLETED',completed_at=CURRENT_TIMESTAMP WHERE room_id=?",
                        (rid,),
                    )


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

    threading.Thread(target=loop, daemon=True, name="guder-game-engine").start()


@app.before_request
def ensure_engine_started():
    start_game_engine()


# ============================================================
# WEBSITE, AUDIO, HEALTH AND CONFIG
# ============================================================

@app.get("/")
def home():
    return send_from_directory(BASE_DIR, "index.html", conditional=True)


@app.get("/health")
def health():
    try:
        with db_read() as conn:
            conn.execute("SELECT 1")
        return jsonify(status="ok", service="Guder Bingo API", database="connected")
    except Exception:
        log.exception("Health check failed")
        return jsonify(status="error", service="Guder Bingo API", database="error"), 500


@app.get("/bingo_amharic_audio/<path:filename>")
def amharic_audio(filename):
    directory = os.path.join(BASE_DIR, "bingo_amharic_audio")
    if not os.path.isdir(directory):
        abort(404)
    response = send_from_directory(directory, filename, conditional=True)
    response.headers["Cache-Control"] = "public, max-age=3600"
    return response


@app.get("/audio/<path:filename>")
def audio_alias(filename):
    # Support both the current and legacy audio folder names.
    for folder in ("bingo_amharic_audio", "audio", os.path.join("static", "audio")):
        directory = os.path.join(BASE_DIR, folder)
        if os.path.isdir(directory) and os.path.isfile(os.path.join(directory, filename)):
            return send_from_directory(directory, filename, conditional=True)
    abort(404)


@app.get("/api/config")
def config():
    return jsonify(
        success=True,
        status="success",
        telebirr_phone=PAYMENT_PHONE,
        min_play_balance=MIN_PLAY_BALANCE,
        cartela_stake=CARTELA_STAKE,
        max_cartela_no=MAX_CARTELAS,
        selection_seconds=SELECTION_SECONDS,
        preparation_seconds=PREPARATION_SECONDS,
        winner_seconds=WINNER_SECONDS,
        number_call_interval=NUMBER_CALL_INTERVAL,
        deposit_verification="PROVISIONAL_UNTIL_MANAGER_REVIEW",
        chapa_enabled=False,
        manager_pin_required=True,
    )


# ============================================================
# PLAYER PROFILE / WALLET
# ============================================================

@app.post("/api/profile/phone")
def update_phone():
    user, error = require_user()
    if error:
        return error

    data = request.get_json(silent=True) or {}
    phone = re.sub(r"[\s\-]", "", str(data.get("phone", "")))
    if not re.fullmatch(r"\+?[0-9]{9,15}", phone):
        return jsonify(success=False, error="Enter a valid phone number."), 400

    with db_transaction() as conn:
        conn.execute(
            "UPDATE users SET phone_number=? WHERE user_id=?",
            (phone, user["user_id"]),
        )
    return jsonify(success=True, phone=phone, message="Phone number saved.")


@app.get("/api/wallet")
def wallet():
    user, error = require_user()
    if error:
        return error

    with db_read() as conn:
        balance = balance_for(conn, user["user_id"])
        transactions = conn.execute(
            """SELECT txn_id,amount,txn_type,reference_id,created_at
               FROM ledger_transactions WHERE user_id=?
               ORDER BY created_at DESC LIMIT 50""",
            (user["user_id"],),
        ).fetchall()

    return jsonify(
        success=True,
        status="success",
        balance=balance,
        transactions=[dict(row) for row in transactions],
        can_select_cartela=balance >= MIN_PLAY_BALANCE,
    )


# ============================================================
# DEPOSIT RECEIPT SUBMISSION
# A submitted receipt gives provisional playing credit immediately.
# Manager can later verify or reverse it.
# ============================================================

def extract_receipt_details(receipt, supplied_amount=None, supplied_reference=None):
    amount = None
    reference = str(supplied_reference or "").strip()

    if supplied_amount not in (None, ""):
        try:
            amount = float(supplied_amount)
        except (TypeError, ValueError):
            return None

    if amount is None:
        amount_patterns = [
            r"(?:amount|paid|birr|ETB|total)\s*[:\-]?\s*([0-9,]+(?:\.[0-9]{1,2})?)",
            r"([0-9,]+(?:\.[0-9]{1,2})?)\s*(?:ETB|Birr)",
            r"([0-9,]+(?:\.[0-9]{1,2})?)\s*ብር",
        ]
        for pattern in amount_patterns:
            match = re.search(pattern, receipt, re.I)
            if match:
                try:
                    amount = float(match.group(1).replace(",", ""))
                    break
                except ValueError:
                    pass

    if not reference:
        reference_patterns = [
            r"(?:transaction\s*(?:id|no|number|reference)?|reference|txn\s*id|receipt\s*no)\s*[:#\-]?\s*([A-Z0-9\-]{5,50})",
            r"(?:የግብይት\s*(?:ቁጥር|መለያ)|የደረሰኝ\s*ቁጥር)\s*[:#\-]?\s*([A-Z0-9\-]{5,50})",
        ]
        for pattern in reference_patterns:
            match = re.search(pattern, receipt, re.I)
            if match:
                reference = match.group(1).strip()
                break

    if amount is None or not reference:
        return None
    if not (MIN_DEPOSIT <= amount <= MAX_DEPOSIT):
        return None
    if len(reference) < 5 or len(reference) > 50:
        return None
    return round(amount, 2), reference


def record_provisional_deposit(conn, user_id, amount, reference, receipt):
    cur = conn.execute(
        """INSERT INTO provisional_deposits(
               user_id,amount,payer_reference,receipt_text,status
           ) VALUES(?,?,?,?,'PROVISIONAL')""",
        (user_id, amount, reference, receipt),
    )
    deposit_id = cur.lastrowid
    add_ledger_entry(
        conn, user_id, amount, "PROVISIONAL_DEPOSIT", f"deposit_{deposit_id}"
    )
    return deposit_id


@app.post("/api/deposit")
def player_deposit():
    user, error = require_user()
    if error:
        return error

    data = request.get_json(silent=True) or {}
    receipt = str(data.get("receipt", data.get("receipt_text", ""))).strip()
    details = extract_receipt_details(
        receipt,
        data.get("amount"),
        data.get("payer_reference", data.get("reference")),
    )
    if len(receipt) < 5 or len(receipt) > 5000 or not details:
        return jsonify(
            success=False,
            error="Submit the receipt text, amount and transaction reference.",
            error_am="የደረሰኙን ጽሑፍ፣ መጠን እና የግብይት ቁጥር ያስገቡ።",
        ), 400

    amount, reference = details
    try:
        with db_transaction() as conn:
            deposit_id = record_provisional_deposit(
                conn, user["user_id"], amount, reference, receipt
            )
    except sqlite3.IntegrityError:
        return jsonify(success=False, error="This transaction reference has already been submitted."), 409

    return jsonify(
        success=True,
        status="PROVISIONAL",
        amount=amount,
        reference=reference,
        deposit_id=deposit_id,
        balance_message="Provisional playing credit added immediately.",
        message="Receipt submitted. You can select a cartela now.",
        message_am="ደረሰኙ ተልኳል። አሁን ካርቴላ መምረጥ ይችላሉ።",
    )


@app.post("/api/bot/deposit")
def bot_deposit():
    supplied = request.headers.get("X-Bot-Secret", "")
    if not BOT_API_SECRET or not hmac.compare_digest(supplied, BOT_API_SECRET):
        return jsonify(success=False, error="Unauthorized bot request."), 401

    data = request.get_json(silent=True) or {}
    telegram_id = str(data.get("user_id", "")).strip()
    receipt = str(data.get("receipt", "")).strip()
    if not telegram_id.isdigit():
        return jsonify(success=False, error="Invalid Telegram user."), 400
    if len(receipt) < 10 or len(receipt) > 5000:
        return jsonify(success=False, error="Receipt text is incomplete."), 400

    details = extract_receipt_details(
        receipt, data.get("amount"), data.get("payer_reference", data.get("reference"))
    )
    if not details:
        return jsonify(success=False, error="Could not identify payment amount and transaction reference."), 400

    amount, reference = details
    try:
        with db_transaction() as conn:
            conn.execute(
                """INSERT INTO users(telegram_id,first_name,username)
                   VALUES(?,?,?) ON CONFLICT(telegram_id) DO UPDATE SET
                   first_name=excluded.first_name,username=excluded.username""",
                (
                    telegram_id,
                    str(data.get("first_name", ""))[:100],
                    str(data.get("username", ""))[:100],
                ),
            )
            user = conn.execute(
                "SELECT * FROM users WHERE telegram_id=?", (telegram_id,)
            ).fetchone()
            deposit_id = record_provisional_deposit(
                conn, user["user_id"], amount, reference, receipt
            )
    except sqlite3.IntegrityError:
        return jsonify(success=False, error="This transaction reference has already been submitted."), 409

    return jsonify(
        success=True,
        status="PROVISIONAL",
        amount=amount,
        reference=reference,
        deposit_id=deposit_id,
        message="Receipt submitted. Provisional playing credit added immediately.",
    )


@app.get("/api/deposits/<int:deposit_id>")
def deposit_status(deposit_id):
    user, error = require_user()
    if error:
        return error

    with db_read() as conn:
        row = conn.execute(
            "SELECT deposit_id,amount,payer_reference,status,admin_note,created_at,reviewed_at FROM provisional_deposits WHERE deposit_id=? AND user_id=?",
            (deposit_id, user["user_id"]),
        ).fetchone()
    if not row:
        return jsonify(success=False, error="Deposit not found."), 404
    return jsonify(success=True, status="success", deposit=dict(row))


# ============================================================
# MANAGER AUTHENTICATION AND DASHBOARD
# Manager access is PIN-protected. Do not expose the PIN in HTML.
# ============================================================

def manager_authorized():
    supplied = request.headers.get("X-Manager-PIN", "")
    return bool(ADMIN_PIN) and hmac.compare_digest(supplied, ADMIN_PIN)


def manager_guard():
    if not manager_authorized():
        return jsonify(
            success=False,
            status="error",
            error="Manager authentication failed.",
            error_am="د مدیر د ننوتلو کوډ ناسم دی.",
        ), 401
    return None


@app.get("/api/manager/overview")
def manager_overview():
    denied = manager_guard()
    if denied:
        return denied

    with db_read() as conn:
        room = conn.execute(
            "SELECT * FROM game_rooms ORDER BY room_id DESC LIMIT 1"
        ).fetchone()
        deposits = conn.execute(
            """SELECT d.*,u.telegram_id,u.first_name,u.username,u.phone_number
               FROM provisional_deposits d JOIN users u ON u.user_id=d.user_id
               ORDER BY d.created_at DESC LIMIT 100"""
        ).fetchall()
        winners = conn.execute(
            """SELECT gr.result_id,gr.room_id,gr.cartela_no,gr.prize_amount,
                      gr.created_at,gr.winning_pattern,u.first_name,u.username,
                      u.phone_number,u.telegram_id
               FROM game_results gr JOIN users u ON u.user_id=gr.user_id
               ORDER BY gr.created_at DESC LIMIT 200"""
        ).fetchall()
        players = conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"]
        deposit_count = conn.execute("SELECT COUNT(*) AS n FROM provisional_deposits").fetchone()["n"]
        total_deposits = conn.execute(
            "SELECT COALESCE(SUM(amount),0) AS n FROM provisional_deposits WHERE status IN ('PROVISIONAL','VERIFIED')"
        ).fetchone()["n"]
        transactions = conn.execute(
            """SELECT l.*,u.first_name,u.username,u.telegram_id
               FROM ledger_transactions l JOIN users u ON u.user_id=l.user_id
               ORDER BY l.created_at DESC LIMIT 100"""
        ).fetchall()

    deposit_list = []
    for row in deposits:
        item = dict(row)
        item["id"] = item["deposit_id"]
        item["reference"] = item["payer_reference"]
        deposit_list.append(item)

    winner_list = []
    for index, row in enumerate(winners, start=1):
        item = dict(row)
        item["row_no"] = index
        item["winner_name"] = item.get("first_name") or item.get("username") or item.get("telegram_id")
        item["session"] = item["room_id"]
        item["date"] = item["created_at"]
        winner_list.append(item)

    return jsonify(
        success=True,
        status="success",
        room_id=room["room_id"] if room else None,
        current_room=room["room_id"] if room else None,
        game_status=room["status"] if room else "WAITING",
        players=players,
        deposit_count=deposit_count,
        total_deposits=round(float(total_deposits), 2),
        deposits=deposit_list,
        winners=winner_list,
        transactions=[dict(row) for row in transactions],
    )


@app.post("/api/manager/deposit/<int:deposit_id>/verify")
def manager_verify_deposit(deposit_id):
    denied = manager_guard()
    if denied:
        return denied

    with db_transaction() as conn:
        deposit = conn.execute(
            "SELECT * FROM provisional_deposits WHERE deposit_id=?", (deposit_id,)
        ).fetchone()
        if not deposit:
            return jsonify(success=False, error="Deposit not found."), 404
        if deposit["status"] != "PROVISIONAL":
            return jsonify(success=False, error="Deposit has already been reviewed."), 409
        conn.execute(
            """UPDATE provisional_deposits SET status='VERIFIED',
               admin_note='Payment independently confirmed',reviewed_at=CURRENT_TIMESTAMP
               WHERE deposit_id=?""",
            (deposit_id,),
        )
    return jsonify(success=True, message="Deposit verified successfully.")


@app.post("/api/manager/deposit/<int:deposit_id>/reject")
def manager_reject_deposit(deposit_id):
    denied = manager_guard()
    if denied:
        return denied

    with db_transaction() as conn:
        deposit = conn.execute(
            "SELECT * FROM provisional_deposits WHERE deposit_id=?", (deposit_id,)
        ).fetchone()
        if not deposit:
            return jsonify(success=False, error="Deposit not found."), 404
        if deposit["status"] != "PROVISIONAL":
            return jsonify(success=False, error="Deposit has already been reviewed."), 409

        add_ledger_entry(
            conn, deposit["user_id"], -float(deposit["amount"]),
            "DEPOSIT_REVERSAL", f"deposit_{deposit_id}"
        )
        conn.execute(
            """UPDATE provisional_deposits SET status='REJECTED',
               admin_note='Payment could not be verified',reviewed_at=CURRENT_TIMESTAMP
               WHERE deposit_id=?""",
            (deposit_id,),
        )
    return jsonify(success=True, message="Deposit rejected and provisional credit reversed.")


@app.get("/api/manager/winners")
def manager_winners():
    denied = manager_guard()
    if denied:
        return denied
    with db_read() as conn:
        rows = conn.execute(
            """SELECT gr.room_id,gr.cartela_no,gr.prize_amount,gr.created_at,
                      gr.winning_pattern,u.first_name,u.username,u.phone_number,u.telegram_id
               FROM game_results gr JOIN users u ON u.user_id=gr.user_id
               ORDER BY gr.created_at DESC"""
        ).fetchall()
    result = []
    for i, row in enumerate(rows, 1):
        item = dict(row)
        item.update({
            "row_no": i,
            "winner_name": item["first_name"] or item["username"] or item["telegram_id"],
            "session": item["room_id"],
            "date": item["created_at"],
        })
        result.append(item)
    return jsonify(success=True, winners=result)


# ============================================================
# GAME STATE - PUBLIC LIVE VIEW; PERSONAL BALANCE ONLY FOR PLAYER
# ============================================================

@app.get("/api/game/state")
def game_state():
    user = authenticated_user()

    with db_read() as conn:
        active = conn.execute(
            """SELECT * FROM game_rooms
               WHERE status IN ('OPEN','REGISTRATION','PREPARATION','PLAYING')
               ORDER BY room_id DESC LIMIT 1"""
        ).fetchone()
        latest = active or conn.execute(
            "SELECT * FROM game_rooms ORDER BY room_id DESC LIMIT 1"
        ).fetchone()

        if not latest:
            return jsonify(
                success=True, status="success", game_status="WAITING",
                room=None, room_id=None, selections=[], called_numbers=[], winners=[],
                available_cartelas=list(range(1, MAX_CARTELAS + 1)),
            )

        rid = latest["room_id"]
        selections = conn.execute(
            """SELECT c.cartela_no,c.numbers_json,u.telegram_id,u.first_name,u.username
               FROM cartelas c JOIN users u ON u.user_id=c.user_id
               WHERE c.room_id=? ORDER BY c.cartela_no""",
            (rid,),
        ).fetchall()
        calls = conn.execute(
            "SELECT number,called_at FROM called_numbers WHERE room_id=? ORDER BY call_id",
            (rid,),
        ).fetchall()
        winners = conn.execute(
            """SELECT gr.room_id,gr.cartela_no,gr.prize_amount,gr.created_at,
                      u.first_name,u.username,u.telegram_id
               FROM game_results gr JOIN users u ON u.user_id=gr.user_id
               ORDER BY gr.created_at DESC LIMIT 10"""
        ).fetchall()

        balance = balance_for(conn, user["user_id"]) if user else 0
        own = conn.execute(
            """SELECT cartela_no,numbers_json FROM cartelas
               WHERE room_id=? AND user_id=? ORDER BY cartela_id DESC LIMIT 1""",
            (rid, user["user_id"]),
        ).fetchone() if user else None

        taken = {int(row["cartela_no"]) for row in selections}

    status_map = {
        "OPEN": "OPEN", "REGISTRATION": "OPEN", "PREPARATION": "PREPARING",
        "PLAYING": "RUNNING", "COMPLETED": "COMPLETED", "CANCELLED": "COMPLETED",
    }
    game_status = status_map.get(latest["status"], "WAITING")
    now = int(time.time())
    remaining = 0

    if game_status == "OPEN" and latest["selection_ends_at"]:
        remaining = max(0, int(latest["selection_ends_at"]) - now)
    elif game_status == "PREPARING" and latest["preparation_ends_at"]:
        remaining = max(0, int(latest["preparation_ends_at"]) - now)
    elif game_status == "COMPLETED" and latest["completed_at"]:
        try:
            completed = int(time.mktime(time.strptime(latest["completed_at"], "%Y-%m-%d %H:%M:%S")))
            remaining = max(0, WINNER_SECONDS - (now - completed))
        except (ValueError, TypeError):
            remaining = 0

    selection_data = [
        {
            "cartela_number": row["cartela_no"],
            "cartela_no": row["cartela_no"],
            "telegram_user_id": row["telegram_id"],
            "first_name": row["first_name"],
            "username": row["username"],
            "numbers": json.loads(row["numbers_json"]),
        }
        for row in selections
    ]
    winner_data = [
        {
            "room_id": row["room_id"],
            "winner_name": row["first_name"] or row["username"] or row["telegram_id"],
            "cartela_no": row["cartela_no"],
            "prize_amount": row["prize_amount"],
            "created_at": row["created_at"],
        }
        for row in winners
    ]

    return jsonify(
        success=True,
        status="success",
        game_status=game_status,
        room_id=rid,
        room=dict(latest),
        remaining_seconds=remaining,
        selections=selection_data,
        called_numbers=[row["number"] for row in calls],
        winners=winner_data,
        balance=balance,
        cartela={
            "cartela_no": own["cartela_no"],
            "numbers_json": own["numbers_json"],
            "numbers": json.loads(own["numbers_json"]),
        } if own else None,
        can_play=bool(user and balance >= MIN_PLAY_BALANCE),
        insufficient_balance=bool(user and balance < MIN_PLAY_BALANCE),
        max_cartela_no=MAX_CARTELAS,
        available_cartelas=[n for n in range(1, MAX_CARTELAS + 1) if n not in taken],
        winner_display_seconds=WINNER_SECONDS if game_status == "COMPLETED" and latest["winner_user_id"] else 0,
    )


@app.get("/api/cartelas/available")
def available_cartelas():
    with db_read() as conn:
        room = conn.execute(
            "SELECT * FROM game_rooms WHERE status IN ('OPEN','REGISTRATION') ORDER BY room_id DESC LIMIT 1"
        ).fetchone()
        if not room:
            return jsonify(success=True, room_id=None, max_cartela_no=MAX_CARTELAS, available_cartelas=[])
        taken = {
            row["cartela_no"] for row in conn.execute(
                "SELECT cartela_no FROM cartelas WHERE room_id=?", (room["room_id"],)
            ).fetchall()
        }
    return jsonify(
        success=True,
        room_id=room["room_id"],
        max_cartela_no=MAX_CARTELAS,
        available_cartelas=[n for n in range(1, MAX_CARTELAS + 1) if n not in taken],
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
        cartela_no = int(data.get("cartela_number", data.get("cartela_no")))
    except (ValueError, TypeError):
        return jsonify(success=False, error="Invalid cartela number."), 400

    if not 1 <= cartela_no <= MAX_CARTELAS:
        return jsonify(success=False, error="Cartela number must be between 1 and 800."), 400

    with db_transaction() as conn:
        balance = balance_for(conn, user["user_id"])
        if balance < MIN_PLAY_BALANCE:
            return jsonify(
                success=False,
                status="insufficient_balance",
                error="Insufficient balance.",
                error_am="በቂ ቀሪ ሂሳብ የለዎትም።",
                message="Please deposit before selecting a cartela.",
                message_am="ካርቴላ ከመምረጥዎ በፊት ገንዘብ ያስገቡ።",
            ), 403

        room = conn.execute(
            "SELECT * FROM game_rooms WHERE status IN ('OPEN','REGISTRATION') ORDER BY room_id DESC LIMIT 1"
        ).fetchone()
        if not room:
            return jsonify(success=False, error="Cartela selection is closed."), 409
        if room["selection_ends_at"] and int(time.time()) >= int(room["selection_ends_at"]):
            return jsonify(success=False, error="The selection period has ended."), 409

        # One cartela per player in a game session.
        already_selected = conn.execute(
            "SELECT cartela_no FROM cartelas WHERE room_id=? AND user_id=? LIMIT 1",
            (room["room_id"], user["user_id"]),
        ).fetchone()
        if already_selected:
            return jsonify(
                success=False,
                error="You have already selected a cartela for this game.",
                existing_cartela=already_selected["cartela_no"],
            ), 409

        matrix = generate_cartela(room["room_id"], cartela_no)
        try:
            conn.execute(
                "INSERT INTO cartelas(room_id,cartela_no,user_id,numbers_json) VALUES(?,?,?,?)",
                (room["room_id"], cartela_no, user["user_id"], json.dumps(matrix)),
            )
        except sqlite3.IntegrityError:
            return jsonify(success=False, error="This cartela is already selected."), 409

        add_ledger_entry(
            conn, user["user_id"], -CARTELA_STAKE, "CARTELA_STAKE",
            f"room_{room['room_id']}_cartela_{cartela_no}",
        )

    return jsonify(
        success=True,
        status="success",
        message="Cartela selected successfully.",
        message_am="ካርቴላውን በተሳካ ሁኔታ መርጠዋል።",
        cartela_number=cartela_no,
        numbers=matrix,
        room_id=room["room_id"],
        selection_color="green",
    )


# ============================================================
# BINGO CLAIM AND WINNER RECORD
# ============================================================

@app.post("/api/game/claim")
def claim_bingo():
    user, error = require_user()
    if error:
        return error

    with db_transaction() as conn:
        room = conn.execute(
            "SELECT * FROM game_rooms WHERE status='PLAYING' ORDER BY room_id DESC LIMIT 1"
        ).fetchone()
        if not room:
            return jsonify(success=False, error="There is no active game."), 409

        card = conn.execute(
            "SELECT * FROM cartelas WHERE room_id=? AND user_id=? ORDER BY cartela_id DESC LIMIT 1",
            (room["room_id"], user["user_id"]),
        ).fetchone()
        if not card:
            return jsonify(success=False, error="You have not selected a cartela."), 403

        matrix = json.loads(card["numbers_json"])
        called = {
            row["number"] for row in conn.execute(
                "SELECT number FROM called_numbers WHERE room_id=?", (room["room_id"],)
            ).fetchall()
        }
        patterns = winning_patterns(matrix, called)
        if not patterns:
            return jsonify(
                success=False,
                error="Bingo could not be verified.",
                error_am="ቢንጎዎ አልተረጋገጠም።",
            ), 400

        # Record prize pool based on paid cartela stakes. Payout remains manual.
        player_count = conn.execute(
            "SELECT COUNT(*) AS n FROM cartelas WHERE room_id=?", (room["room_id"],)
        ).fetchone()["n"]
        prize_amount = round(player_count * CARTELA_STAKE * PRIZE_RATE, 2)

        changed = conn.execute(
            """UPDATE game_rooms
               SET status='COMPLETED',winner_user_id=?,winner_cartela=?,
                   prize_amount=?,completed_at=CURRENT_TIMESTAMP
               WHERE room_id=? AND status='PLAYING'""",
            (user["user_id"], card["cartela_no"], prize_amount, room["room_id"]),
        ).rowcount
        if changed != 1:
            return jsonify(success=False, error="Another winning claim has already been processed."), 409

        conn.execute(
            """INSERT INTO game_results(room_id,user_id,cartela_no,winning_pattern,prize_amount)
               VALUES(?,?,?,?,?)""",
            (room["room_id"], user["user_id"], card["cartela_no"], ",".join(patterns), prize_amount),
        )

    return jsonify(
        success=True,
        status="winner",
        message="Bingo claim verified.",
        message_am="ቢንጎዎ ተረጋግጧል። እንኳን ደስ አለዎት!",
        winner=user["first_name"] or user["username"] or user["telegram_id"],
        cartela_no=card["cartela_no"],
        winning_patterns=patterns,
        prize_amount=prize_amount,
        celebration_seconds=WINNER_SECONDS,
    )


# ============================================================
# GAME HISTORY
# ============================================================

@app.get("/api/game/history")
def game_history():
    with db_read() as conn:
        rows = conn.execute(
            """SELECT gr.room_id,gr.cartela_no,gr.winning_pattern,gr.prize_amount,
                      gr.created_at,u.first_name,u.username,u.phone_number,u.telegram_id
               FROM game_results gr JOIN users u ON u.user_id=gr.user_id
               ORDER BY gr.created_at DESC LIMIT 100"""
        ).fetchall()

    winners = [
        {
            "room_id": row["room_id"],
            "session": row["room_id"],
            "winner_name": row["first_name"] or row["username"] or row["telegram_id"],
            "phone": row["phone_number"],
            "cartela_no": row["cartela_no"],
            "prize_amount": row["prize_amount"],
            "created_at": row["created_at"],
            "date": row["created_at"],
            "winning_pattern": row["winning_pattern"],
        }
        for row in rows
    ]
    return jsonify(success=True, status="success", winners=winners, history=winners)


# ============================================================
# ERRORS AND STARTUP
# ============================================================

@app.errorhandler(413)
def request_too_large(error):
    return jsonify(success=False, error="Request is too large."), 413


@app.errorhandler(404)
def not_found(error):
    if request.path.startswith("/api/"):
        return jsonify(success=False, error="API endpoint not found."), 404
    return error


@app.errorhandler(500)
def internal_error(error):
    log.exception("Internal server error")
    return jsonify(success=False, error="Internal server error."), 500


init_db()


if __name__ == "__main__":
    start_game_engine()
    port = int(os.environ.get("PORT", "5000"))
    app.run(
        host="0.0.0.0",
        port=port,
        debug=(APP_ENV == "development"),
        threaded=True,
    )
