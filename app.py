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
from flask import Flask, request, jsonify, send_from_directory


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

PAYMENT_PHONE = os.environ.get(
    "TELEBIRR_NUMBER",
    "0913706670"
)

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
# DATABASE
# ============================================================

def connect_db():

    conn = sqlite3.connect(
        DB_PATH,
        timeout=20
    )

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


def init_db():

    conn = connect_db()

    try:

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
                """
                INSERT INTO game_rooms(status, selection_ends_at)
                VALUES('OPEN', ?)
                """,
                (int(time.time()) + SELECTION_SECONDS,)
            )

        conn.commit()

    finally:
        conn.close()


# ============================================================
# TELEGRAM AUTHENTICATION
# ============================================================

def verify_telegram_init_data(init_data):

    if not BOT_TOKEN or not init_data:
        return None

    try:

        values = dict(
            parse_qsl(
                init_data,
                keep_blank_values=True
            )
        )

        received_hash = values.pop("hash", None)

        if not received_hash:
            return None

        auth_date = int(values.get("auth_date", "0"))

        now = int(time.time())

        if auth_date <= 0 or now - auth_date > 86400:
            return None

        if auth_date - now > 60:
            return None

        check_string = "\n".join(
            f"{key}={value}"
            for key, value in sorted(values.items())
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

        if not user.get("id"):
            return None

        return user

    except Exception:

        log.exception("Telegram authentication failed")

        return None


def authenticated_user():

    tg_user = verify_telegram_init_data(
        request.headers.get("X-Telegram-Init-Data", "")
    )

    if not tg_user and APP_ENV == "development":

        test_id = request.headers.get(
            "X-Test-User", ""
        ).strip()

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

        conn.execute(
            """
            INSERT INTO users(
                telegram_id,
                first_name,
                username
            )
            VALUES(?,?,?)

            ON CONFLICT(telegram_id)
            DO UPDATE SET
                first_name=excluded.first_name,
                username=excluded.username
            """,
            (
                telegram_id,
                str(tg_user.get("first_name", ""))[:100],
                str(tg_user.get("username", ""))[:100]
            )
        )

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
                status="error",
                message="Telegram authentication failed."
            ),
            401
        )

    return user, None


# ============================================================
# WALLET
# ============================================================

def balance_for(conn, user_id):

    row = conn.execute(
        """
        SELECT COALESCE(SUM(amount),0) AS balance
        FROM ledger_transactions
        WHERE user_id=?
        """,
        (user_id,)
    ).fetchone()

    return round(float(row["balance"]), 2)


def add_ledger_entry(
    conn,
    user_id,
    amount,
    txn_type,
    reference
):

    conn.execute(
        """
        INSERT INTO ledger_transactions(
            txn_id,
            user_id,
            amount,
            txn_type,
            reference_id
        )
        VALUES(?,?,?,?,?)
        """,
        (
            f"{txn_type}_{reference}_{time.time_ns()}",
            user_id,
            amount,
            txn_type,
            f"{txn_type}_{reference}"
        )
    )


# ============================================================
# CARTELA GENERATOR
# ============================================================

def generate_cartela(room_id, cartela_no):

    rng = random.Random(
        f"{room_id}-{cartela_no}-GUDER"
    )

    columns = []

    for letter in "BINGO":

        columns.append(
            sorted(
                rng.sample(
                    list(BINGO_RANGES[letter]),
                    5
                )
            )
        )

    matrix = []

    for row in range(5):

        line = [
            columns[col][row]
            for col in range(5)
        ]

        matrix.append(line)

    matrix[2][2] = "FREE"

    return matrix


def valid_cartela(matrix):

    if not isinstance(matrix, list) or len(matrix) != 5:
        return False

    if any(not isinstance(row, list) or len(row) != 5 for row in matrix):
        return False

    if str(matrix[2][2]).upper() != "FREE":
        return False

    used = set()

    for row in range(5):

        for col in range(5):

            if row == 2 and col == 2:
                continue

            try:
                number = int(matrix[row][col])
            except (ValueError, TypeError):
                return False

            if number not in BINGO_RANGES["BINGO"[col]]:
                return False

            if number in used:
                return False

            used.add(number)

    return True


def winning_patterns(matrix, called):

    called = set(int(n) for n in called)

    lines = []

    for row in range(5):
        lines.append(matrix[row])

    for col in range(5):
        lines.append([
            matrix[row][col]
            for row in range(5)
        ])

    lines.append([
        matrix[i][i] for i in range(5)
    ])

    lines.append([
        matrix[i][4-i] for i in range(5)
    ])

    result = []

    for index, line in enumerate(lines):

        if all(
            str(value).upper() == "FREE"
            or int(value) in called
            for value in line
        ):

            result.append(f"line_{index+1}")

    return result


# ============================================================
# GAME ENGINE
# ============================================================

def create_next_room(conn):

    conn.execute(
        """
        INSERT INTO game_rooms(
            status,
            selection_ends_at
        )
        VALUES('OPEN',?)
        """,
        (int(time.time()) + SELECTION_SECONDS,)
    )


def game_engine_once():

    now = int(time.time())

    with db_transaction() as conn:

        room = conn.execute(
            """
            SELECT *
            FROM game_rooms
            WHERE status IN (
                'OPEN',
                'REGISTRATION',
                'PREPARATION',
                'PLAYING'
            )
            ORDER BY room_id DESC
            LIMIT 1
            """
        ).fetchone()

        if not room:

            latest = conn.execute(
                """
                SELECT *
                FROM game_rooms
                ORDER BY room_id DESC
                LIMIT 1
                """
            ).fetchone()

            if latest:

                if latest["status"] == "COMPLETED":

                    completed = conn.execute(
                        """
                        SELECT strftime('%s', completed_at) AS t
                        FROM game_rooms
                        WHERE room_id=?
                        """,
                        (latest["room_id"],)
                    ).fetchone()

                    if completed and completed["t"]:

                        if now - int(completed["t"]) >= WINNER_SECONDS:
                            create_next_room(conn)

                elif latest["status"] == "CANCELLED":

                    create_next_room(conn)

            else:

                create_next_room(conn)

            return

        room_id = room["room_id"]
        status = room["status"]

        if status in ("OPEN", "REGISTRATION"):

            end = room["selection_ends_at"]

            if end is None:

                conn.execute(
                    """
                    UPDATE game_rooms
                    SET selection_ends_at=?
                    WHERE room_id=?
                    """,
                    (now + SELECTION_SECONDS, room_id)
                )

            elif now >= int(end):

                conn.execute(
                    """
                    UPDATE game_rooms
                    SET status='PREPARATION',
                        preparation_ends_at=?
                    WHERE room_id=?
                    """,
                    (now + PREPARATION_SECONDS, room_id)
                )

        elif status == "PREPARATION":

            end = room["preparation_ends_at"]

            if end and now >= int(end):

                conn.execute(
                    """
                    UPDATE game_rooms
                    SET status='PLAYING'
                    WHERE room_id=?
                    """,
                    (room_id,)
                )

        elif status == "PLAYING":

            last = conn.execute(
                """
                SELECT strftime('%s',called_at) AS t
                FROM called_numbers
                WHERE room_id=?
                ORDER BY call_id DESC
                LIMIT 1
                """,
                (room_id,)
            ).fetchone()

            last_time = int(last["t"]) if last and last["t"] else 0

            if now - last_time >= NUMBER_CALL_INTERVAL:

                called = {
                    row["number"]
                    for row in conn.execute(
                        "SELECT number FROM called_numbers WHERE room_id=?",
                        (room_id,)
                    ).fetchall()
                }

                remaining = [
                    n for n in range(1, 76)
                    if n not in called
                ]

                if remaining:

                    number = random.choice(remaining)

                    conn.execute(
                        """
                        INSERT INTO called_numbers(room_id,number)
                        VALUES(?,?)
                        """,
                        (room_id, number)
                    )

                else:

                    conn.execute(
                        """
                        UPDATE game_rooms
                        SET status='COMPLETED',
                            completed_at=CURRENT_TIMESTAMP
                        WHERE room_id=?
                        """,
                        (room_id,)
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

    threading.Thread(
        target=loop,
        daemon=True,
        name="guder-game-engine"
    ).start()


@app.before_request
def ensure_engine_started():

    start_game_engine()


# ============================================================
# WEBSITE AND HEALTH
# ============================================================

@app.get("/")
def home():

    return send_from_directory(
        BASE_DIR,
        "index.html"
    )


@app.get("/health")
def health():

    return jsonify(
        status="ok",
        service="Guder Bingo API"
    )


@app.get("/api/config")
def config():

    return jsonify(
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

    with connect_db() as conn:

        balance = balance_for(
            conn,
            user["user_id"]
        )

        transactions = conn.execute(
            """
            SELECT *
            FROM ledger_transactions
            WHERE user_id=?
            ORDER BY created_at DESC
            LIMIT 50
            """,
            (user["user_id"],)
        ).fetchall()

    return jsonify(
        status="success",
        balance=balance,
        transactions=[dict(x) for x in transactions]
    )


# ============================================================
# TELEBIRR RECEIPT PARSING
# ============================================================

def extract_receipt_details(receipt):

    amount_match = re.search(
        r"(?:amount|paid|birr|ETB|total)\s*[:\-]?\s*([0-9,]+(?:\.[0-9]{1,2})?)",
        receipt,
        re.IGNORECASE
    )

    reference_match = re.search(
        r"(?:transaction\s*(?:id|no|number|reference)?|reference|txn\s*id|receipt\s*no)\s*[:#\-]?\s*([A-Z0-9\-]{5,50})",
        receipt,
        re.IGNORECASE
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


# ============================================================
# BOT DEPOSIT API
# ============================================================

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

            conn.execute(
                """
                INSERT INTO users(
                    telegram_id,
                    first_name,
                    username
                )
                VALUES(?,?,?)

                ON CONFLICT(telegram_id)
                DO UPDATE SET
                    first_name=excluded.first_name,
                    username=excluded.username
                """,
                (
                    telegram_id,
                    str(data.get("first_name", ""))[:100],
                    str(data.get("username", ""))[:100]
                )
            )

            user = conn.execute(
                "SELECT * FROM users WHERE telegram_id=?",
                (telegram_id,)
            ).fetchone()

            cur = conn.execute(
                """
                INSERT INTO provisional_deposits(
                    user_id,
                    amount,
                    payer_reference,
                    receipt_text,
                    status
                )
                VALUES(?,?,?,?, 'PROVISIONAL')
                """,
                (
                    user["user_id"],
                    amount,
                    reference,
                    receipt
                )
            )

            deposit_id = cur.lastrowid

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
        amount=amount,
        reference=reference,
        deposit_id=deposit_id,
        status="PROVISIONAL",
        message="Provisional credit recorded. Payment awaits independent verification."
    )


# ============================================================
# PLAYER DEPOSIT STATUS
# ============================================================

@app.get("/api/deposits/<int:deposit_id>")
def deposit_status(deposit_id):

    user, error = require_user()

    if error:
        return error

    with connect_db() as conn:

        row = conn.execute(
            """
            SELECT *
            FROM provisional_deposits
            WHERE deposit_id=? AND user_id=?
            """,
            (deposit_id, user["user_id"])
        ).fetchone()

    if not row:

        return jsonify(
            status="error",
            message="Deposit not found."
        ), 404

    return jsonify(
        status="success",
        deposit=dict(row)
    )


# ============================================================
# MANAGER AUTHENTICATION
# ============================================================

def manager_authorized():

    supplied = request.headers.get(
        "X-Manager-PIN",
        ""
    )

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


# ============================================================
# MANAGER OVERVIEW
# ============================================================

@app.get("/api/manager/overview")
def manager_overview():

    denied = manager_guard()

    if denied:
        return denied

    with connect_db() as conn:

        room = conn.execute(
            """
            SELECT *
            FROM game_rooms
            ORDER BY room_id DESC
            LIMIT 1
            """
        ).fetchone()

        deposits = conn.execute(
            """
            SELECT d.*, u.telegram_id, u.first_name, u.username
            FROM provisional_deposits d
            JOIN users u ON u.user_id=d.user_id
            ORDER BY d.created_at DESC
            LIMIT 100
            """
        ).fetchall()

        players = conn.execute(
            "SELECT COUNT(*) AS n FROM users"
        ).fetchone()["n"]

        deposit_count = conn.execute(
            "SELECT COUNT(*) AS n FROM provisional_deposits"
        ).fetchone()["n"]

    return jsonify(
        success=True,
        status="success",
        room_id=room["room_id"] if room else None,
        current_room=room["room_id"] if room else None,
        game_status=room["status"] if room else "WAITING",
        players=players,
        deposit_count=deposit_count,
        deposits=[dict(x) for x in deposits]
    )


# ============================================================
# MANAGER VERIFY DEPOSIT
# ============================================================

@app.post("/api/manager/deposit/<int:deposit_id>/verify")
def manager_verify_deposit(deposit_id):

    denied = manager_guard()

    if denied:
        return denied

    with db_transaction() as conn:

        deposit = conn.execute(
            """
            SELECT *
            FROM provisional_deposits
            WHERE deposit_id=?
            """,
            (deposit_id,)
        ).fetchone()

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

        conn.execute(
            """
            UPDATE provisional_deposits
            SET status='VERIFIED',
                admin_note='Payment independently confirmed',
                reviewed_at=CURRENT_TIMESTAMP
            WHERE deposit_id=?
            """,
            (deposit_id,)
        )

    return jsonify(
        success=True,
        message="Deposit verified successfully."
    )


# ============================================================
# MANAGER REJECT DEPOSIT
# ============================================================

@app.post("/api/manager/deposit/<int:deposit_id>/reject")
def manager_reject_deposit(deposit_id):

    denied = manager_guard()

    if denied:
        return denied

    with db_transaction() as conn:

        deposit = conn.execute(
            """
            SELECT *
            FROM provisional_deposits
            WHERE deposit_id=?
            """,
            (deposit_id,)
        ).fetchone()

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

        # Reverse the provisional credit.
        # If the player already spent the credit, the wallet
        # may become negative and further selection is blocked.

        add_ledger_entry(
            conn,
            deposit["user_id"],
            -float(deposit["amount"]),
            "DEPOSIT_REVERSAL",
            f"deposit_{deposit_id}"
        )

        conn.execute(
            """
            UPDATE provisional_deposits
            SET status='REJECTED',
                admin_note='Payment could not be verified',
                reviewed_at=CURRENT_TIMESTAMP
            WHERE deposit_id=?
            """,
            (deposit_id,)
        )

    return jsonify(
        success=True,
        message="Deposit rejected and provisional credit reversed."
    )


# ============================================================
# GAME STATE
# ============================================================

@app.get("/api/game/state")
def game_state():

    user = authenticated_user()

    with connect_db() as conn:

        room = conn.execute(
            """
            SELECT *
            FROM game_rooms
            WHERE status IN (
                'OPEN',
                'REGISTRATION',
                'PREPARATION',
                'PLAYING'
            )
            ORDER BY room_id DESC
            LIMIT 1
            """
        ).fetchone()

        latest = room or conn.execute(
            """
            SELECT *
            FROM game_rooms
            ORDER BY room_id DESC
            LIMIT 1
            """
        ).fetchone()

        if not latest:

            return jsonify(
                success=True,
                status="success",
                game_status="WAITING",
                room=None,
                selections=[],
                called_numbers=[]
            )

        room_id = latest["room_id"]

        selections = conn.execute(
            """
            SELECT c.cartela_no, c.numbers_json,
                   u.telegram_id, u.first_name, u.username
            FROM cartelas c
            JOIN users u ON u.user_id=c.user_id
            WHERE c.room_id=?
            ORDER BY c.cartela_no
            """,
            (room_id,)
        ).fetchall()

        calls = conn.execute(
            """
            SELECT number, called_at
            FROM called_numbers
            WHERE room_id=?
            ORDER BY call_id
            """,
            (room_id,)
        ).fetchall()

        winners = conn.execute(
            """
            SELECT gr.room_id, gr.cartela_no,
                   gr.created_at,
                   u.first_name, u.username, u.telegram_id
            FROM game_results gr
            JOIN users u ON u.user_id=gr.user_id
            ORDER BY gr.created_at DESC
            LIMIT 5
            """
        ).fetchall()

        balance = (
            balance_for(conn, user["user_id"])
            if user else 0
        )

        own_card = None

        if user:

            own_card = conn.execute(
                """
                SELECT cartela_no, numbers_json
                FROM cartelas
                WHERE room_id=? AND user_id=?
                ORDER BY cartela_id DESC
                LIMIT 1
                """,
                (room_id, user["user_id"])
            ).fetchone()

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
            remaining = 0

    selection_data = []

    for item in selections:

        selection_data.append({
            "cartela_number": item["cartela_no"],
            "cartela_no": item["cartela_no"],
            "telegram_user_id": item["telegram_id"],
            "first_name": item["first_name"],
            "username": item["username"],
            "numbers": json.loads(item["numbers_json"])
        })

    winner_data = []

    for item in winners:

        winner_data.append({
            "room_id": item["room_id"],
            "winner_name": (
                item["first_name"]
                or item["username"]
                or item["telegram_id"]
            ),
            "cartela_no": item["cartela_no"],
            "created_at": item["created_at"]
        })

    return jsonify(
        success=True,
        status="success",
        game_status=game_status,
        room_id=room_id,
        room=dict(latest),
        remaining_seconds=remaining,
        selections=selection_data,
        called_numbers=[x["number"] for x in calls],
        winners=winner_data,
        balance=balance,
        cartela=dict(own_card) if own_card else None,
        can_play=bool(user and balance >= MIN_PLAY_BALANCE)
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
            data.get("cartela_number", data.get("cartela_no"))
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

        room = conn.execute(
            """
            SELECT *
            FROM game_rooms
            WHERE status IN ('OPEN','REGISTRATION')
            ORDER BY room_id DESC
            LIMIT 1
            """
        ).fetchone()

        if not room:

            return jsonify(
                success=False,
                error="Cartela selection is closed."
            ), 409

        if room["selection_ends_at"] and int(time.time()) >= int(room["selection_ends_at"]):

            return jsonify(
                success=False,
                error="The selection period has ended."
            ), 409

        matrix = generate_cartela(
            room["room_id"],
            cartela_no
        )

        try:

            conn.execute(
                """
                INSERT INTO cartelas(
                    room_id,
                    cartela_no,
                    user_id,
                    numbers_json
                )
                VALUES(?,?,?,?)
                """,
                (
                    room["room_id"],
                    cartela_no,
                    user["user_id"],
                    json.dumps(matrix)
                )
            )

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

        room = conn.execute(
            """
            SELECT *
            FROM game_rooms
            WHERE status='PLAYING'
            ORDER BY room_id DESC
            LIMIT 1
            """
        ).fetchone()

        if not room:

            return jsonify(
                success=False,
                error="There is no active game."
            ), 409

        card = conn.execute(
            """
            SELECT *
            FROM cartelas
            WHERE room_id=? AND user_id=?
            ORDER BY cartela_id DESC
            LIMIT 1
            """,
            (
                room["room_id"],
                user["user_id"]
            )
        ).fetchone()

        if not card:

            return jsonify(
                success=False,
                error="You have not selected a cartela."
            ), 403

        matrix = json.loads(
            card["numbers_json"]
        )

        called = {
            x["number"]
            for x in conn.execute(
                """
                SELECT number
                FROM called_numbers
                WHERE room_id=?
                """,
                (room["room_id"],)
            ).fetchall()
        }

        patterns = winning_patterns(
            matrix,
            called
        )

        if not patterns:

            return jsonify(
                success=False,
                error="Bingo could not be verified."
            ), 400

        changed = conn.execute(
            """
            UPDATE game_rooms
            SET status='COMPLETED',
                winner_user_id=?,
                winner_cartela=?,
                completed_at=CURRENT_TIMESTAMP
            WHERE room_id=? AND status='PLAYING'
            """,
            (
                user["user_id"],
                card["cartela_no"],
                room["room_id"]
            )
        ).rowcount

        if changed != 1:

            return jsonify(
                success=False,
                error="Another winning claim has already been processed."
            ), 409

        conn.execute(
            """
            INSERT INTO game_results(
                room_id,
                user_id,
                cartela_no,
                winning_pattern
            )
            VALUES(?,?,?,?)
            """,
            (
                room["room_id"],
                user["user_id"],
                card["cartela_no"],
                ",".join(patterns)
            )
        )

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
# GAME HISTORY
# ============================================================

@app.get("/api/game/history")
def game_history():

    with connect_db() as conn:

        rows = conn.execute(
            """
            SELECT gr.room_id,
                   gr.cartela_no,
                   gr.winning_pattern,
                   gr.created_at,
                   u.first_name,
                   u.username,
                   u.phone_number,
                   u.telegram_id
            FROM game_results gr
            JOIN users u ON u.user_id=gr.user_id
            ORDER BY gr.created_at DESC
            LIMIT 100
            """
        ).fetchall()

    winners = []

    for row in rows:

        winners.append({
            "room_id": row["room_id"],
            "winner_name": (
                row["first_name"]
                or row["username"]
                or row["telegram_id"]
            ),
            "phone": row["phone_number"],
            "cartela_no": row["cartela_no"],
            "prize_amount": 0,
            "created_at": row["created_at"]
        })

    return jsonify(
        success=True,
        status="success",
        winners=winners,
        history=winners
    )


# ============================================================
# ERROR HANDLERS
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


# ============================================================
# INITIALIZATION
# ============================================================

init_db()


if __name__ == "__main__":

    start_game_engine()

    port = int(
        os.environ.get("PORT", "5000")
    )

    app.run(
        host="0.0.0.0",
        port=port,
        debug=(APP_ENV == "development")
    )
