import os
import time
import random
import sqlite3
import threading
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

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

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS cartela_selections (
            selection_id INTEGER PRIMARY KEY AUTOINCREMENT,
            room_id INTEGER,
            user_id INTEGER,
            cartela_no INTEGER,
            numbers_json TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(room_id, cartela_no)
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS called_numbers (
            call_id INTEGER PRIMARY KEY AUTOINCREMENT,
            room_id INTEGER,
            number INTEGER,
            called_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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

def get_or_create_telegram_user(init_data):
    conn = sqlite3.connect('guder_bingo.db', timeout=10.0)
    cursor = conn.cursor()
    
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

# Background Game Engine Thread for automated number calling
def background_game_engine():
    while True:
        try:
            conn = sqlite3.connect('guder_bingo.db', timeout=10.0)
            cursor = conn.cursor()
            
            cursor.execute("SELECT room_id, status FROM game_rooms ORDER BY room_id DESC LIMIT 1")
            room = cursor.fetchone()
            
            if room:
                room_id, status = room
                if status == 'OPEN':
                    cursor.execute("SELECT COUNT(*) FROM cartela_selections WHERE room_id = ?", (room_id,))
                    count = cursor.fetchone()[0]
                    if count > 0:
                        cursor.execute("UPDATE game_rooms SET status = 'RUNNING' WHERE room_id = ?", (room_id,))
                        conn.commit()
                elif status == 'RUNNING':
                    cursor.execute("SELECT number FROM called_numbers WHERE room_id = ?", (room_id,))
                    called = {row[0] for row in cursor.fetchall()}
                    
                    available = [n for n in range(1, 76) if n not in called]
                    if available:
                        next_num = random.choice(available)
                        cursor.execute("INSERT INTO called_numbers (room_id, number) VALUES (?, ?)", (room_id, next_num))
                        conn.commit()
            conn.close()
        except Exception as e:
            print("Engine error:", e)
        time.sleep(4)

threading.Thread(target=background_game_engine, daemon=True).start()

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
    
    cartela = None
    called_numbers = []
    
    if room:
        r_id = room['room_id']
        cursor.execute('SELECT cartela_no, numbers_json FROM cartela_selections WHERE room_id = ? AND user_id = ?', (r_id, user_id))
        c_row = cursor.fetchone()
        if c_row:
            cartela = {"cartela_no": c_row[0], "numbers_json": c_row[1]}
            
        cursor.execute('SELECT number, called_at FROM called_numbers WHERE room_id = ? ORDER BY call_id ASC', (r_id,))
        called_numbers = [{"number": row[0], "called_at": row[1]} for row in cursor.fetchall()]
        
    conn.close()

    return jsonify({
        "status": "success",
        "balance": balance,
        "room": room,
        "cartela": cartela,
        "called_numbers": called_numbers
    })

@app.route('/api/game/history', methods=['GET'])
def get_game_history():
    init_data = request.headers.get('X-Telegram-Init-Data', '')
    user_id = get_or_create_telegram_user(init_data)
    
    conn = sqlite3.connect('guder_bingo.db', timeout=10.0)
    cursor = conn.cursor()
    cursor.execute('''
        SELECT room_id, cartela_no, created_at FROM cartela_selections 
        WHERE user_id = ? ORDER BY selection_id DESC LIMIT 20
    ''', (user_id,))
    history = [{"room_id": row[0], "cartela_no": row[1], "created_at": row[2]} for row in cursor.fetchall()]
    conn.close()
    
    return jsonify({"status": "success", "history": history})

@app.route('/api/cartela/select', methods=['POST'])
def select_cartela_endpoint():
    data = request.json
    init_data = request.headers.get('X-Telegram-Init-Data', '')
    user_id = get_or_create_telegram_user(init_data)
    
    room_id = data.get('room_id', 1)
    cartela_no = data.get('cartela_no')
    import json
    numbers_matrix = json.dumps(data.get('numbers'))
    
    conn = sqlite3.connect('guder_bingo.db', timeout=10.0)
    cursor = conn.cursor()
    try:
        cursor.execute("BEGIN EXCLUSIVE TRANSACTION;")
        
        stake = 10.0
        current_bal = get_user_balance(user_id)
        if current_bal < stake:
            conn.rollback()
            return jsonify({"status": "error", "message": "Insufficient wallet balance to select cartela."}), 400
            
        txn_id = f"stake_{room_id}_{user_id}_{int(time.time())}"
        cursor.execute('''
            INSERT INTO ledger_transactions (txn_id, user_id, amount, type, reference_id)
            VALUES (?, ?, ?, ?, ?)
        ''', (txn_id, user_id, -stake, 'CARTELA_STAKE', f"room_{room_id}"))
        
        cursor.execute('''
            INSERT OR REPLACE INTO cartela_selections (room_id, user_id, cartela_no, numbers_json)
            VALUES (?, ?, ?, ?)
        ''', (room_id, user_id, cartela_no, numbers_matrix))
        
        cursor.execute('UPDATE game_rooms SET total_pot = total_pot + ? WHERE room_id = ?', (stake, room_id))
        
        conn.commit()
        return jsonify({"status": "success", "message": f"Cartela #{cartela_no} selected successfully!"})
    except Exception as e:
        conn.rollback()
        return jsonify({"status": "error", "message": str(e)}), 400
    finally:
        conn.close()

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
        cursor.execute('SELECT status, total_pot FROM game_rooms WHERE room_id = ?', (room_id,))
        room = cursor.fetchone()
        if not room or room[0] == 'COMPLETED':
            conn.rollback()
            return jsonify({"status": "error", "message": "Round already won or room closed!"}), 400

        cursor.execute('UPDATE game_rooms SET status = ? WHERE room_id = ?', ('COMPLETED', room_id))

        total_pot = room[1] if room[1] > 0 else 100.0
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
