import os
import sqlite3
import json
from flask import Flask, request, jsonify, g
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

DB_PATH = os.path.join(os.path.dirname(__file__), 'bingo.db')

def get_db():
    db = getattr(g, '_database', None)
    if db is None:
        db = g._database = sqlite3.connect(DB_PATH)
        db.row_factory = sqlite3.Row
    return db

@app.teardown_appcontext
def close_connection(exception):
    db = getattr(g, '_database', None)
    if db is not None:
        db.close()

def init_db():
    with app.app_context():
        db = get_db()
        cursor = db.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS rooms (
                room_id INTEGER PRIMARY KEY AUTOINCREMENT,
                status TEXT DEFAULT 'OPEN'
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS cartelas (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                room_id INTEGER,
                cartela_no INTEGER,
                numbers_json TEXT
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS called_numbers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                room_id INTEGER,
                number INTEGER
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS wallets (
                user_id TEXT PRIMARY KEY,
                balance REAL DEFAULT 100.0
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT,
                txn_type TEXT,
                amount REAL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        cursor.execute('SELECT COUNT(*) FROM rooms')
        if cursor.fetchone()[0] == 0:
            cursor.execute("INSERT INTO rooms (status) VALUES ('OPEN')")
        db.commit()

init_db()

@app.route('/')
def home():
    return "Guder Bingo Backend is Running Successfully!"

@app.route('/api/config', methods=['GET'])
def get_config():
    return jsonify({"telebirr_phone": "0913706670"})

@app.route('/api/wallet', methods=['GET'])
def get_wallet():
    user_id = request.headers.get('X-Telegram-Init-Data', 'default_user')
    db = get_db()
    cursor = db.cursor()
    cursor.execute('SELECT balance FROM wallets WHERE user_id = ?', (user_id,))
    row = cursor.fetchone()
    if not row:
        cursor.execute('INSERT INTO wallets (user_id, balance) VALUES (?, ?)', (user_id, 100.0))
        db.commit()
        balance = 100.0
    else:
        balance = row['balance']

    cursor.execute('SELECT txn_type, amount, created_at FROM transactions WHERE user_id = ? ORDER BY id DESC LIMIT 10', (user_id,))
    txns = [dict(t) for t in cursor.fetchall()]
    return jsonify({"balance": balance, "transactions": txns})

@app.route('/api/deposit', methods=['POST'])
def deposit():
    data = request.json
    amount = data.get('amount', 0)
    user_id = request.headers.get('X-Telegram-Init-Data', 'default_user')
    
    db = get_db()
    cursor = db.cursor()
    cursor.execute('SELECT balance FROM wallets WHERE user_id = ?', (user_id,))
    if not cursor.fetchone():
        cursor.execute('INSERT INTO wallets (user_id, balance) VALUES (?, ?)', (user_id, 100.0))

    cursor.execute("UPDATE wallets SET balance = balance + ? WHERE user_id = ?", (amount, user_id))
    cursor.execute("INSERT INTO transactions (user_id, txn_type, amount) VALUES (?, ?, ?)", (user_id, 'DEPOSIT', amount))
    db.commit()
    
    return jsonify({"success": True, "message": f"የ {amount} ብር ደረሰኝዎ በተሳካ ሁኔታ ተመዝግቧል!"})

@app.route('/api/game/state', methods=['GET'])
def game_state():
    db = get_db()
    cursor = db.cursor()
    cursor.execute("SELECT * FROM rooms ORDER BY room_id DESC LIMIT 1")
    room = cursor.fetchone()
    
    user_id = request.headers.get('X-Telegram-Init-Data', 'default_user')
    cursor.execute('SELECT balance FROM wallets WHERE user_id = ?', (user_id,))
    w = cursor.fetchone()
    balance = w['balance'] if w else 100.0

    cartela = None
    called_numbers = []
    if room:
        cursor.execute("SELECT * FROM cartelas WHERE room_id = ? ORDER BY id DESC LIMIT 1", (room['room_id'],))
        cartela = cursor.fetchone()
        
        cursor.execute("SELECT number FROM called_numbers WHERE room_id = ?", (room['room_id'],))
        called_numbers = [dict(row) for row in cursor.fetchall()]

    return jsonify({
        "balance": balance,
        "room": dict(room) if room else None,
        "cartela": dict(cartela) if cartela else None,
        "called_numbers": called_numbers
    })

@app.route('/api/game/history', methods=['GET'])
def game_history():
    user_id = request.headers.get('X-Telegram-Init-Data', 'default_user')
    db = get_db()
    cursor = db.cursor()
    cursor.execute("SELECT txn_type, amount, created_at FROM transactions WHERE user_id = ? ORDER BY id DESC LIMIT 20", (user_id,))
    history = [dict(row) for row in cursor.fetchall()]
    return jsonify({"history": history})

@app.route('/api/cartela/select', methods=['POST'])
def select_cartela():
    data = request.json
    room_id = data.get('room_id')
    cartela_no = data.get('cartela_no')
    numbers = data.get('numbers')
    
    db = get_db()
    cursor = db.cursor()
    cursor.execute("INSERT INTO cartelas (room_id, cartela_no, numbers_json) VALUES (?, ?, ?)", 
                   (room_id, cartela_no, json.dumps(numbers)))
    db.commit()
    return jsonify({"success": True, "message": f"Cartela #{cartela_no} selected successfully."})

@app.route('/api/claim-bingo', methods=['POST'])
def claim_bingo():
    data = request.json
    room_id = data.get('room_id')
    cartela_no = data.get('cartela_no')
    
    prize = 50.0
    user_id = request.headers.get('X-Telegram-Init-Data', 'default_user')
    
    db = get_db()
    cursor = db.cursor()
    cursor.execute("UPDATE wallets SET balance = balance + ? WHERE user_id = ?", (prize, user_id))
    cursor.execute("INSERT INTO transactions (user_id, txn_type, amount) VALUES (?, ?, ?)", (user_id, 'WIN', prize))
    db.commit()
    
    return jsonify({"success": True, "prize_allocated": prize})

@app.route('/api/manager/sessions', methods=['GET'])
def manager_sessions():
    db = get_db()
    cursor = db.cursor()
    cursor.execute("SELECT user_id as phone_number, amount FROM transactions WHERE txn_type = 'WIN' ORDER BY id DESC LIMIT 5")
    winners = [dict(row) for row in cursor.fetchall()]
    return jsonify({"winners": winners})

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)
