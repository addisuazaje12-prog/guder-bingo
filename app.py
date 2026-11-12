import os
import time
import sqlite3
from flask import Flask, request, jsonify, send_from_directory

app = Flask(__name__)

# Manual Deposit & Verification Configuration
TELEBIRR_SHORT_CODE = "800001"
CBE_ACCOUNT = "1000000000000"

def init_db():
    conn = sqlite3.connect('guder_bingo.db', timeout=10.0)
    cursor = conn.cursor()
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id TEXT UNIQUE,
            phone_number TEXT UNIQUE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS game_rooms (
            room_id INTEGER PRIMARY KEY AUTOINCREMENT,
            status TEXT DEFAULT 'OPEN',
            total_pot REAL DEFAULT 0.0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    cursor.execute("INSERT OR IGNORE INTO game_rooms (room_id, status, total_pot) VALUES (1, 'OPEN', 100.0)")

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

init_db()

@app.route('/')
def serve_frontend():
    return send_from_directory('.', 'index.html')

def get_user_balance(user_id):
    conn = sqlite3.connect('guder_bingo.db', timeout=10.0)
    cursor = conn.cursor()
    cursor.execute('SELECT SUM(amount) FROM ledger_transactions WHERE user_id = ?', (user_id,))
    result = cursor.fetchone()
    conn.close()
    return result[0] if result[0] is not None else 0.0

# Helper to get or create default test user for Telegram Web App
def get_or_create_telegram_user(init_data):
    conn = sqlite3.connect('guder_bingo.db', timeout=10.0)
    cursor = conn.cursor()
    
    # Extract or fallback to default test user
    telegram_id = "test_user_tg"
    if init_data and "id=" in init_data:
        try:
            import urllib.parse
            parsed = urllib.parse.parse_qs(init_data)
            if 'user' in parsed:
                import json
                user_json = json.loads(parsed['user'][0])
                telegram_id = str(user_json.get('id', 'test_user_tg'))
        except Exception:
            pass

    cursor.execute('SELECT user_id FROM users WHERE telegram_id = ?', (telegram_id,))
    row = cursor.fetchone()
    if row:
        user_id = row[0]
    else:
        cursor.execute('INSERT INTO users (telegram_id, phone_number) VALUES (?, ?)', (telegram_id, '251913706670'))
        user_id = cursor.lastrowid
        conn.commit()
    
    conn.close()
    return user_id

@app.route('/api/wallet', methods=['GET'])
def get_wallet():
    init_data = request.headers.get('X-Telegram-Init-Data', '')
    user_id = get_or_create_telegram_user(init_data)
    balance = get_user_balance(user_id)
    
    transactions = []
    conn = sqlite3.connect('guder_bingo.db', timeout=10.0)
    cursor = conn.cursor()
    cursor.execute('SELECT txn_id, amount, type, created_at FROM ledger_transactions WHERE user_id = ? ORDER BY created_at DESC LIMIT 20', (user_id,))
    for tx in cursor.fetchall():
        transactions.append({"txn_type": tx[2], "amount": tx[1], "created_at": tx[3]})
    conn.close()
    
    return jsonify({"status": "success", "balance": balance, "transactions": transactions})

@app.route('/api/config', methods=['GET'])
def get_config():
    return jsonify({"status": "success", "telebirr_phone": "0913706670", "cbe_account": CBE_ACCOUNT})

@app.route('/api/game/state', methods=['GET'])
def get_game_state():
    init_data = request.headers.get('X-Telegram-Init-Data', '')
    user_id = get_or_create_telegram_user(init_data)
    balance = get_user_balance(user_id)

    conn = sqlite3.connect('guder_bingo.db', timeout=10.0)
    cursor = conn.cursor()
    cursor.execute('SELECT room_id, status, total_pot FROM game_rooms ORDER BY room_id DESC LIMIT 1')
    room_row = cursor.fetchone()
    room = {"room_id": room_row[0], "status": room_row[1], "total_pot": room_row[2]} if room_row else None
    conn.close()

    return jsonify({
        "status": "success",
        "balance": balance,
        "room": room,
        "cartela": None,
        "called_numbers": []
    })

@app.route('/api/game/history', methods=['GET'])
def get_game_history():
    return jsonify({"status": "success", "history": []})

@app.route('/api/cartela/select', methods=['POST'])
def select_cartela_endpoint():
    data = request.json
    return jsonify({"status": "success", "message": "Cartela selected successfully.", "cartela_no": data.get('cartela_no')})

@app.route('/api/deposit', methods=['POST'])
def deposit_funds():
    data = request.json
    init_data = request.headers.get('X-Telegram-Init-Data', '')
    user_id = get_or_create_telegram_user(init_data)
    
    amount = float(data.get('amount', 10.0))
    tx_ref = data.get('payer_reference', f"manual_tx_{user_id}_{int(time.time())}")
    
    conn = sqlite3.connect('guder_bingo.db', timeout=10.0)
    cursor = conn.cursor()
    try:
        cursor.execute("BEGIN EXCLUSIVE TRANSACTION;")
        cursor.execute('''
            INSERT OR IGNORE INTO ledger_transactions (txn_id, user_id, amount, type, reference_id)
            VALUES (?, ?, ?, ?, ?)
        ''', (tx_ref, user_id, amount, 'MANUAL_DEPOSIT', tx_ref))
        conn.commit()
        
        current_balance = get_user_balance(user_id)
        return jsonify({
            "status": "success",
            "message": "Manual deposit submitted successfully.",
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
    data = request.json
    cartela_no = data.get('cartela_no')
    room_id = data.get('room_id', 1)
    init_data = request.headers.get('X-Telegram-Init-Data', '')
    user_id = get_or_create_telegram_user(init_data)

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

        total_pot = 100.0
        prize_allocated = total_pot * 0.50

        txn_id = f"txn_win_{room_id}_{user_id}_{int(time.time() * 1000)}"
        cursor.execute('''
            INSERT INTO ledger_transactions (txn_id, user_id, amount, type, reference_id)
            VALUES (?, ?, ?, ?, ?)
        ''', (txn_id, user_id, prize_allocated, 'BINGO_WIN_PAYOUT', f"room_{room_id}_cartela_{cartela_no}"))

        conn.commit()
        current_balance = get_user_balance(user_id)

        return jsonify({
            "status": "winner",
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
