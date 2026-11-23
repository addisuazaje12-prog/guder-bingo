from datetime import datetime, timezone
import json
import os
import random
import sqlite3
import threading
import time
import urllib.parse
from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS

app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "*"}})

DB_PATH = os.environ.get("DATABASE_PATH", "guder_bingo.db")
MIN_STAKE_ETB = 10.0
MAX_CARTELA_NO = 800

# Manual Deposit & Verification Configuration
TELEBIRR_SHORT_CODE = "800001"
TELEBIRR_PHONE = "0913706670"
CBE_ACCOUNT = "1000000000000"

AUDIO_DIRS = ["bingo_amharic_audio", "audio", "static/audio"]
engine_lock = threading.Lock()


@app.after_request
def add_cors_headers(response):
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type, X-Telegram-Init-Data, X-Telegram-User-Id"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, DELETE, OPTIONS"
    return response


@app.route("/<path:any_path>", methods=["OPTIONS"])
def handle_options(any_path):
    return ("", 204)


def get_db_connection():
    conn = sqlite3.connect(DB_PATH, timeout=20.0, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA busy_timeout=20000;")
    return conn


def init_db():
    conn = get_db_connection()
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id TEXT UNIQUE,
                phone_number TEXT UNIQUE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS game_rooms (
                room_id INTEGER PRIMARY KEY AUTOINCREMENT,
                status TEXT DEFAULT 'OPEN',
                total_pot REAL DEFAULT 0.0,
                phase_start_time REAL DEFAULT 0.0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        cols = [r["name"] for r in conn.execute("PRAGMA table_info(game_rooms)").fetchall()]
        if "phase_start_time" not in cols:
            conn.execute("ALTER TABLE game_rooms ADD COLUMN phase_start_time REAL DEFAULT 0.0")

        room = conn.execute("SELECT room_id FROM game_rooms WHERE room_id = 1").fetchone()
        if not room:
            conn.execute(
                "INSERT INTO game_rooms (room_id, status, total_pot, phase_start_time) VALUES (1, 'OPEN', 0.0, ?)",
                (time.time(),),
            )

        conn.execute("""
            CREATE TABLE IF NOT EXISTS ledger_transactions (
                txn_id TEXT PRIMARY KEY,
                user_id INTEGER,
                amount REAL,
                type TEXT,
                status TEXT DEFAULT 'CONFIRMED',
                reference_id TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(user_id) REFERENCES users(user_id)
            )
        """)

        ledger_cols = [r["name"] for r in conn.execute("PRAGMA table_info(ledger_transactions)").fetchall()]
        if "status" not in ledger_cols:
            conn.execute("ALTER TABLE ledger_transactions ADD COLUMN status TEXT DEFAULT 'CONFIRMED'")

        conn.execute("""
            CREATE TABLE IF NOT EXISTS cartela_selections (
                selection_id INTEGER PRIMARY KEY AUTOINCREMENT,
                room_id INTEGER,
                user_id INTEGER,
                cartela_no INTEGER,
                numbers_json TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(room_id, cartela_no)
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS called_numbers (
                call_id INTEGER PRIMARY KEY AUTOINCREMENT,
                room_id INTEGER,
                number INTEGER,
                called_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS room_winners (
                room_id INTEGER PRIMARY KEY,
                user_id INTEGER,
                cartela_no INTEGER,
                winner_name TEXT,
                prize_allocated REAL DEFAULT 0.0,
                claimed_at TEXT
            )
        """)
    finally:
        conn.close()


init_db()


def get_user_balance_with_conn(conn, user_id):
    row = conn.execute(
        """
        SELECT COALESCE(SUM(amount), 0.0) AS bal
        FROM ledger_transactions
        WHERE user_id = ? AND status = 'CONFIRMED'
        """,
        (user_id,),
    ).fetchone()
    return float(row["bal"]) if row else 0.0


def get_user_balance(user_id):
    conn = get_db_connection()
    try:
        return get_user_balance_with_conn(conn, user_id)
    finally:
        conn.close()


def extract_telegram_id(init_data="", fallback_user_id=""):
    if init_data:
        try:
            parsed = urllib.parse.parse_qs(init_data)
            if "user" in parsed:
                user_obj = json.loads(parsed["user"][0])
                if user_obj.get("id") is not None:
                    return str(user_obj["id"]).strip()
        except Exception:
            pass
    if fallback_user_id and str(fallback_user_id).strip():
        return str(fallback_user_id).strip()
    return "test_user_tg"


def get_or_create_telegram_user(init_data="", fallback_user_id=""):
    telegram_id = extract_telegram_id(init_data, fallback_user_id)
    safe_phone = f"tg_{telegram_id}"

    conn = get_db_connection()
    try:
        conn.execute("BEGIN IMMEDIATE;")
        row = conn.execute(
            "SELECT user_id FROM users WHERE telegram_id = ?",
            (telegram_id,),
        ).fetchone()
        if row:
            user_id = row["user_id"]
        else:
            conn.execute(
                """
                INSERT OR IGNORE INTO users (telegram_id, phone_number)
                VALUES (?, ?)
                """,
                (telegram_id, safe_phone),
            )
            created = conn.execute(
                "SELECT user_id FROM users WHERE telegram_id = ?",
                (telegram_id,),
            ).fetchone()
            user_id = created["user_id"] if created else None
        conn.execute("COMMIT;")
        return user_id
    except Exception:
        try:
            conn.execute("ROLLBACK;")
        except Exception:
            pass
        raise
    finally:
        conn.close()


def resolve_audio_file(filename):
    safe_name = os.path.basename(filename)
    for audio_dir in AUDIO_DIRS:
        if not os.path.isdir(audio_dir):
            continue
        direct_path = os.path.join(audio_dir, safe_name)
        if os.path.isfile(direct_path):
            return audio_dir, safe_name
        target_lower = safe_name.lower()
        for entry in os.listdir(audio_dir):
            if entry.lower() == target_lower and os.path.isfile(os.path.join(audio_dir, entry)):
                return audio_dir, entry
    return None, None


@app.route("/health", methods=["GET"])
def health_check():
    return jsonify({"status": "ok", "db": DB_PATH})


@app.route("/")
def serve_frontend():
    if os.path.exists("index.html"):
        return send_from_directory(".", "index.html")
    return jsonify({"status": "ok", "service": "Guder Bingo API"})


@app.route("/audio/<path:filename>")
def serve_audio(filename):
    audio_dir, matched_name = resolve_audio_file(filename)
    if not audio_dir or not matched_name:
        return jsonify({"status": "error", "message": f"Audio file not found: {filename}"}), 404
    return send_from_directory(audio_dir, matched_name)


def background_game_engine():
    while True:
        if engine_lock.acquire(blocking=False):
            conn = None
            try:
                conn = get_db_connection()
                room = conn.execute(
                    "SELECT room_id, status, total_pot, phase_start_time FROM game_rooms ORDER BY room_id DESC LIMIT 1"
                ).fetchone()

                if room:
                    room_id = room["room_id"]
                    status = room["status"]
                    phase_start_time = float(room["phase_start_time"] or 0.0)
                    now = time.time()

                    if phase_start_time == 0.0:
                        phase_start_time = now
                        conn.execute("BEGIN IMMEDIATE;")
                        conn.execute(
                            "UPDATE game_rooms SET phase_start_time = ? WHERE room_id = ?",
                            (now, room_id),
                        )
                        conn.execute("COMMIT;")

                    elapsed = now - phase_start_time

                    if status == "OPEN" and elapsed >= 30:
                        conn.execute("BEGIN IMMEDIATE;")
                        conn.execute(
                            "UPDATE game_rooms SET status = 'PREPARING', phase_start_time = ? WHERE room_id = ?",
                            (now, room_id),
                        )
                        conn.execute("COMMIT;")
                    elif status == "PREPARING" and elapsed >= 5:
                        conn.execute("BEGIN IMMEDIATE;")
                        conn.execute(
                            "UPDATE game_rooms SET status = 'RUNNING', phase_start_time = ? WHERE room_id = ?",
                            (now, room_id),
                        )
                        conn.execute("COMMIT;")
                    elif status == "RUNNING":
                        rows = conn.execute(
                            "SELECT number FROM called_numbers WHERE room_id = ?", (room_id,)
                        ).fetchall()
                        called = {r["number"] for r in rows}
                        available = [n for n in range(1, 76) if n not in called]
                        if available:
                            next_num = random.choice(available)
                            conn.execute("BEGIN IMMEDIATE;")
                            conn.execute(
                                "INSERT INTO called_numbers (room_id, number) VALUES (?, ?)",
                                (room_id, next_num),
                            )
                            conn.execute("COMMIT;")
                    elif status == "COMPLETED" and elapsed >= 10:
                        conn.execute("BEGIN IMMEDIATE;")
                        conn.execute("DELETE FROM called_numbers WHERE room_id = ?", (room_id,))
                        conn.execute("DELETE FROM cartela_selections WHERE room_id = ?", (room_id,))
                        conn.execute("DELETE FROM room_winners WHERE room_id = ?", (room_id,))
                        conn.execute(
                            "UPDATE game_rooms SET status = 'OPEN', total_pot = 0.0, phase_start_time = ? WHERE room_id = ?",
                            (now, room_id),
                        )
                        conn.execute("COMMIT;")
            except Exception as e:
                if conn:
                    try:
                        conn.execute("ROLLBACK;")
                    except Exception:
                        pass
                print("Engine error:", e)
            finally:
                if conn:
                    conn.close()
                engine_lock.release()
        time.sleep(2)


threading.Thread(target=background_game_engine, daemon=True).start()


@app.route("/api/wallet", methods=["GET"])
def get_wallet():
    init_data = request.headers.get("X-Telegram-Init-Data", "")
    fallback_uid = request.headers.get("X-Telegram-User-Id", "")
    user_id = get_or_create_telegram_user(init_data, fallback_uid)

    conn = get_db_connection()
    try:
        balance = get_user_balance_with_conn(conn, user_id)
        tx_rows = conn.execute(
            """
            SELECT txn_id, amount, type, status, created_at
            FROM ledger_transactions
            WHERE user_id = ?
            ORDER BY created_at DESC LIMIT 20
            """,
            (user_id,),
        ).fetchall()
        transactions = [
            {
                "txn_id": tx["txn_id"],
                "txn_type": tx["type"],
                "amount": tx["amount"],
                "status": tx["status"],
                "created_at": tx["created_at"],
            }
            for tx in tx_rows
        ]
        return jsonify({"status": "success", "balance": balance, "transactions": transactions})
    finally:
        conn.close()


@app.route("/api/config", methods=["GET"])
def get_config():
    return jsonify({
        "status": "success",
        "telebirr_short_code": TELEBIRR_SHORT_CODE,
        "telebirr_phone": TELEBIRR_PHONE,
        "cbe_account": CBE_ACCOUNT,
        "min_stake_etb": MIN_STAKE_ETB,
        "max_cartela_no": MAX_CARTELA_NO,
    })


@app.route("/api/rooms", methods=["GET"])
def get_rooms_list():
    conn = get_db_connection()
    try:
        rows = conn.execute(
            "SELECT room_id, status, total_pot, phase_start_time FROM game_rooms ORDER BY room_id DESC"
        ).fetchall()
        rooms = [
            {
                "room_id": r["room_id"],
                "status": r["status"],
                "total_pot": r["total_pot"],
                "phase_start_time": r["phase_start_time"],
            }
            for r in rows
        ]
        return jsonify({"status": "success", "rooms": rooms})
    finally:
        conn.close()


@app.route("/api/game/state", methods=["GET"])
def get_game_state():
    init_data = request.headers.get("X-Telegram-Init-Data", "")
    fallback_uid = request.headers.get("X-Telegram-User-Id", "")
    user_id = get_or_create_telegram_user(init_data, fallback_uid)

    conn = get_db_connection()
    try:
        balance = get_user_balance_with_conn(conn, user_id)
        room_row = conn.execute(
            "SELECT room_id, status, total_pot, phase_start_time FROM game_rooms ORDER BY room_id DESC LIMIT 1"
        ).fetchone()

        room = None
        cartela = None
        called_numbers = []

        if room_row:
            r_id = room_row["room_id"]
            status = room_row["status"]
            total_pot = room_row["total_pot"]
            phase_start_time = float(room_row["phase_start_time"] or 0.0)
            now = time.time()
            elapsed = now - (phase_start_time if phase_start_time else now)

            selection_remaining = max(0, int(30 - elapsed)) if status == "OPEN" else None
            preparation_remaining = max(0, int(5 - elapsed)) if status == "PREPARING" else None

            room = {
                "room_id": r_id,
                "status": status,
                "total_pot": total_pot,
                "selection_remaining": selection_remaining,
                "preparation_remaining": preparation_remaining,
            }

            c_row = conn.execute(
                "SELECT cartela_no, numbers_json FROM cartela_selections WHERE room_id = ? AND user_id = ?",
                (r_id, user_id),
            ).fetchone()
            if c_row:
                cartela = {"cartela_no": c_row["cartela_no"], "numbers_json": c_row["numbers_json"]}

            n_rows = conn.execute(
                "SELECT number, called_at FROM called_numbers WHERE room_id = ? ORDER BY call_id ASC",
                (r_id,),
            ).fetchall()
            called_numbers = [{"number": r["number"], "called_at": r["called_at"]} for r in n_rows]

        return jsonify({
            "status": "success",
            "balance": balance,
            "room": room,
            "cartela": cartela,
            "called_numbers": called_numbers,
        })
    finally:
        conn.close()


@app.route("/api/game/history", methods=["GET"])
def get_game_history():
    init_data = request.headers.get("X-Telegram-Init-Data", "")
    fallback_uid = request.headers.get("X-Telegram-User-Id", "")
    user_id = get_or_create_telegram_user(init_data, fallback_uid)

    conn = get_db_connection()
    try:
        rows = conn.execute(
            """
            SELECT room_id, cartela_no, created_at
            FROM cartela_selections
            WHERE user_id = ?
            ORDER BY selection_id DESC LIMIT 20
            """,
            (user_id,),
        ).fetchall()
        history = [
            {"room_id": r["room_id"], "cartela_no": r["cartela_no"], "created_at": r["created_at"]}
            for r in rows
        ]
        return jsonify({"status": "success", "history": history})
    finally:
        conn.close()


@app.route("/api/cartela/select", methods=["POST"])
def select_cartela_endpoint():
    data = request.get_json(silent=True) or {}
    init_data = request.headers.get("X-Telegram-Init-Data") or data.get("init_data", "")
    fallback_uid = request.headers.get("X-Telegram-User-Id") or data.get("user_id", "")
    user_id = get_or_create_telegram_user(init_data, fallback_uid)

    try:
        room_id = int(data.get("room_id", 1))
        cartela_no = int(data.get("cartela_no"))
    except (TypeError, ValueError):
        return jsonify({"status": "error", "message": "Invalid room_id or cartela_no."}), 400

    if not (1 <= cartela_no <= MAX_CARTELA_NO):
        return jsonify({
            "status": "error",
            "message": f"Cartela number must be between 1 and {MAX_CARTELA_NO}.",
        }), 400

    numbers_matrix = json.dumps(data.get("numbers"))

    conn = get_db_connection()
    try:
        conn.execute("BEGIN IMMEDIATE;")

        room = conn.execute(
            "SELECT room_id, status FROM game_rooms WHERE room_id = ?", (room_id,)
        ).fetchone()
        if not room or room["status"] != "OPEN":
            conn.execute("ROLLBACK;")
            return jsonify({"status": "error", "message": "Cartela selection is not open for this room."}), 400

        current_bal = get_user_balance_with_conn(conn, user_id)
        if current_bal < MIN_STAKE_ETB:
            conn.execute("ROLLBACK;")
            return jsonify({
                "status": "error",
                "message": f"Insufficient balance ({current_bal:.2f} ETB). Minimum {MIN_STAKE_ETB:.0f} ETB required to select a cartela.",
            }), 400

        taken = conn.execute(
            "SELECT user_id FROM cartela_selections WHERE room_id = ? AND cartela_no = ?",
            (room_id, cartela_no),
        ).fetchone()
        if taken and taken["user_id"] != user_id:
            conn.execute("ROLLBACK;")
            return jsonify({"status": "error", "message": f"Cartela #{cartela_no} is already taken."}), 409

        existing_user_cartela = conn.execute(
            "SELECT cartela_no FROM cartela_selections WHERE room_id = ? AND user_id = ?",
            (room_id, user_id),
        ).fetchone()
        if existing_user_cartela:
            conn.execute("ROLLBACK;")
            return jsonify({"status": "error", "message": "You have already selected a cartela for this round."}), 400

        txn_id = f"stake_{room_id}_{user_id}_{int(time.time() * 1000)}"
        conn.execute(
            """
            INSERT INTO ledger_transactions (txn_id, user_id, amount, type, status, reference_id)
            VALUES (?, ?, ?, 'CARTELA_STAKE', 'CONFIRMED', ?)
            """,
            (txn_id, user_id, -MIN_STAKE_ETB, f"room_{room_id}"),
        )

        conn.execute(
            """
            INSERT INTO cartela_selections (room_id, user_id, cartela_no, numbers_json)
            VALUES (?, ?, ?, ?)
            """,
            (room_id, user_id, cartela_no, numbers_matrix),
        )

        conn.execute(
            "UPDATE game_rooms SET total_pot = total_pot + ? WHERE room_id = ?",
            (MIN_STAKE_ETB, room_id),
        )

        conn.execute("COMMIT;")
        new_bal = get_user_balance(user_id)
        return jsonify({
            "status": "success",
            "message": f"Cartela #{cartela_no} selected successfully!",
            "balance": new_bal,
        })
    except Exception as e:
        try:
            conn.execute("ROLLBACK;")
        except Exception:
            pass
        return jsonify({"status": "error", "message": str(e)}), 400
    finally:
        conn.close()


@app.route("/api/deposit", methods=["POST"])
@app.route("/api/wallet/deposit", methods=["POST"])
def deposit_funds():
    data = request.get_json(silent=True) or {}
    init_data = request.headers.get("X-Telegram-Init-Data") or data.get("init_data", "")
    fallback_uid = request.headers.get("X-Telegram-User-Id") or data.get("user_id", "")
    user_id = get_or_create_telegram_user(init_data, fallback_uid)

    try:
        amount = float(data.get("amount", MIN_STAKE_ETB))
    except (TypeError, ValueError):
        return jsonify({"status": "error", "message": "Invalid deposit amount."}), 400

    if amount <= 0:
        return jsonify({"status": "error", "message": "Deposit amount must be positive."}), 400

    tx_ref = str(data.get("payer_reference") or f"manual_tx_{user_id}_{int(time.time() * 1000)}").strip()

    conn = get_db_connection()
    try:
        conn.execute("BEGIN IMMEDIATE;")
        conn.execute(
            """
            INSERT OR IGNORE INTO ledger_transactions (txn_id, user_id, amount, type, status, reference_id)
            VALUES (?, ?, ?, 'MANUAL_DEPOSIT', 'PENDING', ?)
            """,
            (tx_ref, user_id, amount, tx_ref),
        )
        conn.execute("COMMIT;")
        confirmed_balance = get_user_balance(user_id)

        return jsonify({
            "status": "pending_verification",
            "message": "Deposit receipt logged as provisional/pending verification.",
            "deposit_id": tx_ref,
            "amount": amount,
            "confirmed_balance": confirmed_balance,
        })
    except Exception as e:
        try:
            conn.execute("ROLLBACK;")
        except Exception:
            pass
        return jsonify({"status": "error", "message": str(e)}), 400
    finally:
        conn.close()


@app.route("/api/room/<int:room_id>/winner", methods=["GET"])
def get_room_winner(room_id):
    since = request.args.get("since", "")
    conn = get_db_connection()
    try:
        row = conn.execute(
            """
            SELECT room_id, cartela_no, winner_name, prize_allocated, claimed_at
            FROM room_winners
            WHERE room_id = ?
            """,
            (room_id,),
        ).fetchone()
        if not row:
            return jsonify({"status": "success", "winner": None})

        winner = {
            "room_id": row["room_id"],
            "cartela_no": row["cartela_no"],
            "winner_name": row["winner_name"],
            "prize_allocated": row["prize_allocated"],
            "claimed_at": row["claimed_at"],
        }
        if since and winner["claimed_at"] == since:
            return jsonify({"status": "success", "winner": None})

        return jsonify({"status": "success", "winner": winner})
    finally:
        conn.close()


@app.route("/api/claim-bingo", methods=["POST"])
@app.route("/api/bingo/claim", methods=["POST"])
def claim_bingo():
    data = request.get_json(silent=True) or {}
    init_data = request.headers.get("X-Telegram-Init-Data") or data.get("init_data", "")
    fallback_uid = request.headers.get("X-Telegram-User-Id") or data.get("user_id", "")
    user_id = get_or_create_telegram_user(init_data, fallback_uid)

    try:
        room_id = int(data.get("room_id", 1))
        cartela_no = int(data.get("cartela_no"))
    except (TypeError, ValueError):
        return jsonify({"status": "error", "message": "Invalid room_id or cartela_no."}), 400

    conn = get_db_connection()
    try:
        conn.execute("BEGIN IMMEDIATE;")
        room = conn.execute(
            "SELECT status, total_pot FROM game_rooms WHERE room_id = ?", (room_id,)
        ).fetchone()
        if not room or room["status"] != "RUNNING":
            conn.execute("ROLLBACK;")
            return jsonify({"status": "error", "message": "Bingo can only be claimed while round is RUNNING."}), 400

        selection = conn.execute(
            "SELECT numbers_json FROM cartela_selections WHERE room_id = ? AND user_id = ? AND cartela_no = ?",
            (room_id, user_id, cartela_no),
        ).fetchone()
        if not selection:
            conn.execute("ROLLBACK;")
            return jsonify({"status": "error", "message": "You do not own this cartela in the active round."}), 403

        called_rows = conn.execute(
            "SELECT number FROM called_numbers WHERE room_id = ?", (room_id,)
        ).fetchall()
        called_set = {r["number"] for r in called_rows}

        is_valid_bingo = False
        try:
            grid = json.loads(selection["numbers_json"]) if selection["numbers_json"] else None
            if isinstance(grid, list) and len(grid) == 5 and all(isinstance(r, list) and len(r) == 5 for r in grid):
                marked = [
                    [
                        True if (r_i == 2 and c_i == 2 and grid[r_i][c_i] in (0, None, "FREE")) or (grid[r_i][c_i] in called_set)
                        else False
                        for c_i in range(5)
                    ]
                    for r_i in range(5)
                ]
                row_win = any(all(marked[r]) for r in range(5))
                col_win = any(all(marked[r][c] for r in range(5)) for c in range(5))
                diag1_win = all(marked[i][i] for i in range(5))
                diag2_win = all(marked[i][4 - i] for i in range(5))
                is_valid_bingo = row_win or col_win or diag1_win or diag2_win
        except Exception:
            is_valid_bingo = False

        if not is_valid_bingo:
            conn.execute("ROLLBACK;")
            return jsonify({"status": "error", "message": "Bingo claim not verified against called numbers yet."}), 400

        total_pot = float(room["total_pot"] or 0.0)
        prize_allocated = round(total_pot * 0.50, 2)
        claimed_at = datetime.now(timezone.utc).isoformat()
        winner_name = str(data.get("winner_name") or f"Player (Cartela #{cartela_no})").strip()

        conn.execute(
            "UPDATE game_rooms SET status = 'COMPLETED', phase_start_time = ? WHERE room_id = ?",
            (time.time(), room_id),
        )

        conn.execute(
            """
            INSERT OR REPLACE INTO room_winners (room_id, user_id, cartela_no, winner_name, prize_allocated, claimed_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (room_id, user_id, cartela_no, winner_name, prize_allocated, claimed_at),
        )

        if prize_allocated > 0:
            txn_id = f"txn_win_{room_id}_{user_id}_{int(time.time() * 1000)}"
            conn.execute(
                """
                INSERT INTO ledger_transactions (txn_id, user_id, amount, type, status, reference_id)
                VALUES (?, ?, ?, 'BINGO_WIN_PAYOUT', 'CONFIRMED', ?)
                """,
                (txn_id, user_id, prize_allocated, f"room_{room_id}_cartela_{cartela_no}"),
            )

        conn.execute("COMMIT;")
        current_balance = get_user_balance(user_id)

        return jsonify({
            "status": "winner",
            "message": "Bingo verified successfully!",
            "cartela_no": cartela_no,
            "prize_allocated": prize_allocated,
            "user_balance": current_balance,
            "winner": {
                "room_id": room_id,
                "cartela_no": cartela_no,
                "winner_name": winner_name,
                "prize_allocated": prize_allocated,
                "claimed_at": claimed_at,
            },
        })
    except Exception as e:
        try:
            conn.execute("ROLLBACK;")
        except Exception:
            pass
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        conn.close()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
