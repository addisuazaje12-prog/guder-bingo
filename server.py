import os
from flask import Flask, jsonify, request, send_from_directory

# Get the absolute path of the folder where server.py is located
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
app = Flask(__name__)


@app.route('/')
def serve_frontend():
  return send_from_directory(BASE_DIR, 'index.html')


@app.route('/bingo_amharic_audio/<path:filename>')
def serve_audio(filename):
  return send_from_directory(os.path.join(BASE_DIR, 'bingo_amharic_audio'), filename)


@app.route('/api/claim-bingo', methods=['POST'])
def claim_bingo():
  data = request.get_json() or {}
  cartela_no = data.get('cartela_no')
  # Add your backend reward/database logic here
  return jsonify({'status': 'success', 'prize_allocated': 50.0})


if __name__ == '__main__':
  app.run(host='0.0.0.0', port=5000, debug=True)