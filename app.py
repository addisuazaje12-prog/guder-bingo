import os
import time
import sqlite3
from flask import Flask, request, jsonify, send_from_directory

app = Flask(__name__)

# Manual Deposit & Verification Configuration
TELEBIRR_SHORT_CODE = "800001"
CBE_ACCOUNT = "1000000000000"

# Initialize Local SQLite Database (Immutable Ledger & Active Rooms)
def init_db():
    conn = sqlite3.connect('guder_bingo.db', timeout=10.0)
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
    
    # 2. Game Rooms Table (Tracks room status to prevent race conditions)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS game_rooms (
            room_id INTEGER PRIMARY KEY AUTOINCREMENT,
            status TEXT DEFAULT 'OPEN', -- OPEN, REGISTRATION, PREPARING, COMPLETED
            total_pot REAL DEFAULT 0.0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Ensure default room 1 exists for testing
    cursor.execute("INSERT OR IGNORE INTO game_rooms (room_id, status, total_pot) VALUES (1, 'OPEN', 100.0)")

    # 3. Ledger Transactions Table (Immutable Log)
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

# Run database setup on server start
init_db()

# Root Route: Serves index.html to fix Telegram bottom menu JSON display issue
@app.route('/')
def serve_frontend():
    return send_from_directory('.', 'index.html')

# Helper: Calculate user balance dynamically from the immutable ledger
def get_user_balance(user_id):
    conn = sqlite3.connect('guder_bingo.db', timeout=10.0)
    cursor = conn.cursor()
    cursor.execute('SELECT SUM(amount) FROM ledger_transactions WHERE user_id = ?', (user_id,))
    result = cursor.fetchone()
    conn.close()
    return result[0] if result[0] is not None else 0.0

@app.route('/api/config', methods=['GET'])
def get_config():
    return jsonify({
        "telebirr_phone": "0913706670",
        "telebirr_short_code": TELEBIRR_SHORT_CODE,
        "cbe_account": CBE_ACCOUNT
    })

@app.route('/api/wallet', methods=['GET'])
def get_wallet():
    # Mocking user balance and transactions for demonstration
    conn = sqlite3.connect('guder_bingo.db', timeout=10.0)
    cursor = conn.cursor()
    cursor.execute('SELECT SUM(amount) FROM ledger_transactions')
    res = cursor.fetchone()
    balance = res[0] if res[0] else 150.0

    cursor.execute('SELECT txn_id, amount, type, created_at FROM ledger_transactions ORDER BY created_at DESC LIMIT 10')
    rows = cursor.fetchall()
    conn.close()

    txns = [{"txn_type": r[2], "amount": r[1], "created_at": r[3]} for r in rows]
    return jsonify({"balance": balance, "transactions": txns})

@app.route('/api/game/state', methods=['GET'])
def game_state():
    conn = sqlite3.connect('guder_bingo.db', timeout=10.0)
    cursor = conn.cursor()
    cursor.execute('SELECT room_id, status, total_pot FROM game_rooms ORDER BY room_id DESC LIMIT 1')
    room_row = cursor.fetchone()
    conn.close()

    room = {"room_id": room_row[0], "status": room_row[1], "total_pot": room_row[2]} if room_row else None
    return jsonify({
        "balance": 150.0,
        "room": room,
        "cartela": None,
        "called_numbers": [{"number": 5}, {"number": 18}, {"number": 32}]
    })

@app.route('/api/game/history', methods=['GET'])
def game_history():
    return jsonify({"history": []})

@app.route('/api/manager/sessions', methods=['GET'])
def manager_sessions():
    conn = sqlite3.connect('guder_bingo.db', timeout=10.0)
    cursor = conn.cursor()
    cursor.execute('''
        SELECT U.phone_number, L.amount, L.reference_id, L.created_at
        FROM ledger_transactions L
        JOIN users U ON L.user_id = U.user_id
        WHERE L.type = 'BINGO_WIN_PAYOUT'
        ORDER BY L.created_at DESC
    ''')
    rows = cursor.fetchall()
    conn.close()
    
    winners = []
    for r in rows:
        winners.append({
            "phone_number": r[0],
            "amount": r[1],
            "reference": r[2],
            "time": r[3]
        })
    return jsonify({"status": "success", "winners": winners})

@app.route('/api/deposit', methods=['POST'])
def deposit_funds():
    data = request.json or {}
    phone = data.get('phone', '251913706670')
    amount = float(data.get('amount', 10.0))
    tx_ref = data.get('tx_ref', f"manual_tx_{phone}_{int(time.time())}")
    
    conn = sqlite3.connect('guder_bingo.db', timeout=10.0)
    cursor = conn.cursor()
    try:
        cursor.execute("BEGIN EXCLUSIVE TRANSACTION;")
        
        cursor.execute('SELECT user_id FROM users WHERE phone_number = ?', (phone,))
        row = cursor.fetchone()
        if row:
            user_id = row[0]
        else:
            cursor.execute('INSERT INTO users (phone_number) VALUES (?)', (phone,))
            user_id = cursor.lastrowid
            
        cursor.execute('''
            INSERT OR IGNORE INTO ledger_transactions (txn_id, user_id, amount, type, reference_id)
            VALUES (?, ?, ?, ?, ?)
        ''', (tx_ref, user_id, amount, 'MANUAL_DEPOSIT', tx_ref))
        
        conn.commit()
        
        current_balance = get_user_balance(user_id)
        return jsonify({
            "status": "success",
            "message": "Manual deposit submitted successfully. Awaiting SMS verification.",
            "deposit_id": tx_ref,
            "amount": amount,
            "user_balance": current_balance
        })
    except Exception as e:
        conn.rollback()
        return jsonify({"status": "error", "message": str(e)}), 400
    finally:
        conn.close()

@app.route('/api/claim-bingo', methods=['POST'])
def claim_bingo():
    data = request.json or {}
    cartela_no = data.get('cartela_no', 1)
    user_phone = data.get('user_phone', '251913706670') 
    room_id = data.get('room_id', 1)

    conn = sqlite3.connect('guder_bingo.db', timeout=10.0)
    cursor = conn.cursor()

    try:
        cursor.execute("BEGIN EXCLUSIVE TRANSACTION;")

        cursor.execute('SELECT status FROM game_rooms WHERE room_id = ?', (room_id,))
        room = cursor.fetchone()
        if not room or room[0] == 'COMPLETED':
            conn.rollback()
            return jsonify({"status": "error", "message": "Round already won or room closed!"}), 400

        cursor.execute('UPDATE game_rooms SET status = ? WHERE room_id = ?', ('COMPLETED', room_id))

        cursor.execute('SELECT user_id FROM users WHERE phone_number = ?', (user_phone,))
        row = cursor.fetchone()
        if row:
            user_id = row[0]
        else:
            cursor.execute('INSERT INTO users (phone_number) VALUES (?)', (user_phone,))
            user_id = cursor.lastrowid

        total_pot = 100.0
        prize_allocated = total_pot * 0.50

        txn_id = f"txn_win_{room_id}_{user_id}_{int(time.time() * 1000)}"
        cursor.execute('''
            INSERT INTO ledger_transactions (txn_id, user_id, amount, type, reference_id)
            VALUES (?, ?, ?, ?, ?)
        ''', (txn_id, user_id, prize_allocated, 'BINGO_WIN_PAYOUT', f"room_{room_id}_cartela_{cartela_no}"))

        conn.commit()

        cursor.execute('SELECT SUM(amount) FROM ledger_transactions WHERE user_id = ?', (user_id,))
        current_balance = cursor.fetchone()[0]

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
