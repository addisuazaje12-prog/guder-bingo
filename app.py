import os
import time
import sqlite3
from flask import Flask, request, jsonify, send_from_directory
from chapa import Chapa

app = Flask(__name__)

# Chapa / Payment & Telebirr Configuration
CHAPA_SECRET_KEY = os.environ.get("CHAPA_SECRET_KEY", "YOUR_CHAPA_SECRET_KEY")
chapa = Chapa(CHAPA_SECRET_KEY)

TELEBIRR_APP_ID = os.environ.get("TELEBIRR_APP_ID", "YOUR_APP_ID")
TELEBIRR_APP_KEY = os.environ.get("TELEBIRR_APP_KEY", "YOUR_APP_KEY")
TELEBIRR_SHORT_CODE = os.environ.get("TELEBIRR_SHORT_CODE", "800001")

# Database Path Configuration
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'guder_bingo.db')

# Initialize Local SQLite Database
def init_db():
    conn = sqlite3.connect(DB_PATH, timeout=10.0)
    cursor = conn.cursor()
    
    # 1. Users Table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id TEXT UNIQUE,
            phone_number TEXT UNIQUE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # 2. Game Rooms Table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS game_rooms (
            room_id INTEGER PRIMARY KEY AUTOINCREMENT,
            status TEXT DEFAULT 'OPEN',
            total_pot REAL DEFAULT 0.0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Ensure default room 1 exists
    cursor.execute("INSERT OR IGNORE INTO game_rooms (room_id, status, total_pot) VALUES (1, 'OPEN', 100.0)")

    # 3. Ledger Transactions Table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS ledger_transactions (
            txn_id TEXT PRIMARY KEY,
            user_id INTEGER,
            amount REAL,
            type TEXT,
            reference_id TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(user_id) REFERENCES users(user_id)
        )
    ''')
    
    conn.commit()
    conn.close()

# Run database setup on module load
init_db()

# Root Route: Serves index.html directly from the repository root
@app.route('/')
def serve_frontend():
    return send_from_directory('.', 'index.html')

# Helper: Calculate user balance dynamically from the immutable ledger
def get_user_balance(user_id):
    conn = sqlite3.connect(DB_PATH, timeout=10.0)
    cursor = conn.cursor()
    cursor.execute('SELECT SUM(amount) FROM ledger_transactions WHERE user_id = ?', (user_id,))
    result = cursor.fetchone()
    conn.close()
    return result[0] if (result and result[0] is not None) else 0.0

@app.route('/api/deposit', methods=['POST'])
def deposit_funds():
    data = request.json or {}
    user_email = data.get('email', 'player@guderbingo.com')
    amount = data.get('amount', 10.0)
    phone = data.get('phone', '251913706670')
    
    # Dynamic callback URL based on host
    base_url = os.environ.get("RENDER_EXTERNAL_URL", request.host_url.rstrip('/'))
    callback_url = f"{base_url}/api/payment-callback"

    try:
        response = chapa.initialize(
            email=user_email,
            amount=str(amount),
            first_name=data.get('first_name', 'Player'),
            last_name=data.get('last_name', 'Guder'),
            tx_ref=f"guder_tx_{phone}_{int(time.time())}",
            callback_url=callback_url
        )
        return jsonify(response)
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 400

@app.route('/api/claim-bingo', methods=['POST'])
def claim_bingo():
    data = request.json or {}
    cartela_no = data.get('cartela_no')
    user_phone = data.get('user_phone', '251913706670')  
    room_id = data.get('room_id', 1)

    conn = sqlite3.connect(DB_PATH, timeout=10.0)
    cursor = conn.cursor()

    try:
        # Correct SQLite syntax for locking transactions
        conn.execute("BEGIN EXCLUSIVE")

        # 1. Check if the room has already been won by another player
        cursor.execute('SELECT status FROM game_rooms WHERE room_id = ?', (room_id,))
        room = cursor.fetchone()
        if not room or room[0] == 'COMPLETED':
            conn.rollback()
            return jsonify({"status": "error", "message": "Round already won or room closed!"}), 400

        # Mark room as completed immediately
        cursor.execute('UPDATE game_rooms SET status = ? WHERE room_id = ?', ('COMPLETED', room_id))

        # 2. Find or create user ID
        cursor.execute('SELECT user_id FROM users WHERE phone_number = ?', (user_phone,))
        row = cursor.fetchone()
        if row:
            user_id = row[0]
        else:
            cursor.execute('INSERT INTO users (phone_number) VALUES (?)', (user_phone,))
            user_id = cursor.lastrowid

        # 3. Calculation: 50% pot allocation for the winner
        total_players = 10
        stake_per_cartela = 10.0
        total_pot = total_players * stake_per_cartela
        prize_allocated = total_pot * 0.50

        # 4. Record the payout entry into the ledger
        txn_id = f"txn_win_{room_id}_{user_id}_{int(time.time() * 1000)}"
        cursor.execute('''
            INSERT INTO ledger_transactions (txn_id, user_id, amount, type, reference_id)
            VALUES (?, ?, ?, ?, ?)
        ''', (txn_id, user_id, prize_allocated, 'BINGO_WIN_PAYOUT', f"room_{room_id}_cartela_{cartela_no}"))

        conn.commit()

        # Fetch updated balance
        current_balance = get_user_balance(user_id)

        return jsonify({
            "status": "success",
            "message": "Bingo verified successfully!",
            "cartela_no": cartela_no,
            "prize_allocated": prize_allocated,
            "user_balance": current_balance
        })

    except Exception as e:
        conn.rollback()
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        conn.close()

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
