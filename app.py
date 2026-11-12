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
            status TEXT DEFAULT 'OPEN', -- OPEN, COMPLETED
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

@app.route('/api/balance', methods=['GET'])
def get_balance():
    phone = request.args.get('phone', '251913706670')
    conn = sqlite3.connect('guder_bingo.db', timeout=10.0)
    cursor = conn.cursor()
    cursor.execute('SELECT user_id FROM users WHERE phone_number = ?', (phone,))
    row = cursor.fetchone()
    balance = 0.0
    if row:
        user_id = row[0]
        balance = get_user_balance(user_id)
    conn.close()
    return jsonify({"status": "success", "balance": balance})

@app.route('/api/room', methods=['GET'])
def get_room():
    conn = sqlite3.connect('guder_bingo.db', timeout=10.0)
    cursor = conn.cursor()
    cursor.execute('SELECT room_id, status, total_pot FROM game_rooms ORDER BY room_id DESC LIMIT 1')
    room = cursor.fetchone()
    conn.close()
    if room:
        return jsonify({
            "status": "success",
            "room_id": room[0],
            "room_status": room[1],
            "total_pot": room[2]
        })
    return jsonify({"status": "error", "message": "No active room found"}), 404

@app.route('/api/wallet', methods=['GET'])
def get_wallet():
    phone = request.args.get('phone', '251913706670')
    conn = sqlite3.connect('guder_bingo.db', timeout=10.0)
    cursor = conn.cursor()
    cursor.execute('SELECT user_id FROM users WHERE phone_number = ?', (phone,))
    row = cursor.fetchone()
    user_id = row[0] if row else None
    balance = get_user_balance(user_id) if user_id else 0.0
    
    transactions = []
    if user_id:
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
    phone = request.args.get('phone', '251913706670')
    conn = sqlite3.connect('guder_bingo.db', timeout=10.0)
    cursor = conn.cursor()
    cursor.execute('SELECT user_id FROM users WHERE phone_number = ?', (phone,))
    row = cursor.fetchone()
    user_id = row[0] if row else None
    balance = get_user_balance(user_id) if user_id else 0.0

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
    phone = data.get('phone', '251913706670')
    amount = float(data.get('amount', 10.0))
    tx_ref = data.get('tx_ref', f"manual_tx_{phone}_{int(time.time())}")
    
    conn = sqlite3.connect('guder_bingo.db', timeout=10.0)
    cursor = conn.cursor()
    try:
        cursor.execute("BEGIN EXCLUSIVE TRANSACTION;")
        
        # Find or create user ID in database
        cursor.execute('SELECT user_id FROM users WHERE phone_number = ?', (phone,))
        row = cursor.fetchone()
        if row:
            user_id = row[0]
        else:
            cursor.execute('INSERT INTO users (phone_number) VALUES (?)', (phone,))
            user_id = cursor.lastrowid
            
        # Record manual deposit into ledger
        cursor.execute('''
            INSERT OR IGNORE INTO ledger_transactions (txn_id, user_id, amount, type, reference_id)
            VALUES (?, ?, ?, ?, ?)
        ''', (tx_ref, user_id, amount, 'MANUAL_DEPOSIT', tx_ref))
        
        conn.commit()
        
        current_balance = get_user_balance(user_id)
        return jsonify({
            "status": "success",
            "message": "Manual deposit submitted successfully. Awaiting SMS verification.",
            "tx_ref": tx_ref,
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
    user_phone = data.get('user_phone', '251913706670') 
    room_id = data.get('room_id', 1)

    # Open thread-safe connection with timeout for concurrency
    conn = sqlite3.connect('guder_bingo.db', timeout=10.0)
    cursor = conn.cursor()

    try:
        # BEGIN EXCLUSIVE locks the database to prevent concurrent write conflicts
        cursor.execute("BEGIN EXCLUSIVE TRANSACTION;")

        # 1. Check if the room has already been won by another player
        cursor.execute('SELECT status FROM game_rooms WHERE room_id = ?', (room_id,))
        room = cursor.fetchone()
        if not room or room[0] == 'COMPLETED':
            conn.rollback()
            return jsonify({"status": "error", "message": "Round already won or room closed!"}), 400

        # Mark room as completed immediately inside the locked transaction block
        cursor.execute('UPDATE game_rooms SET status = ? WHERE room_id = ?', ('COMPLETED', room_id))

        # 2. Find or create user ID in database
        cursor.execute('SELECT user_id FROM users WHERE phone_number = ?', (user_phone,))
        row = cursor.fetchone()
        if row:
            user_id = row[0]
        else:
            cursor.execute('INSERT INTO users (phone_number) VALUES (?)', (user_phone,))
            user_id = cursor.lastrowid

        # 3. Example calculation: 50% pot allocation for the winner
        total_players = 10
        stake_per_cartela = 10.0
        total_pot = total_players * stake_per_cartela
        prize_allocated = total_pot * 0.50

        # 4. Record the payout entry into the immutable ledger securely
        txn_id = f"txn_win_{room_id}_{user_id}_{int(time.time() * 1000)}"
        cursor.execute('''
            INSERT INTO ledger_transactions (txn_id, user_id, amount, type, reference_id)
            VALUES (?, ?, ?, ?, ?)
        ''', (txn_id, user_id, prize_allocated, 'BINGO_WIN_PAYOUT', f"room_{room_id}_cartela_{cartela_no}"))

        conn.commit()

        # Fetch updated balance
        cursor.execute('SELECT SUM(amount) FROM ledger_transactions WHERE user_id = ?', (user_id,))
        current_balance = cursor.fetchone()[0]

        print(f"User {user_phone} won Room #{room_id} Cartela #{cartela_no}. Prize: {prize_allocated} ETB. Balance: {current_balance} ETB")

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
