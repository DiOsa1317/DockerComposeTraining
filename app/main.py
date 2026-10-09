import os
from flask import Flask, jsonify
import psycopg2
import redis

app = Flask(__name__)

DB_URL = os.getenv("DATABASE_URL")
REDIS_URL = os.getenv("REDIS_URL")

@app.route("/")
def index():
    r = redis.from_url(REDIS_URL)
    count = r.incr("hits")
    
    conn = psycopg2.connect(DB_URL)
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute("CREATE TABLE IF NOT EXISTS notes (id SERIAL PRIMARY KEY, content TEXT);")
    cur.execute("SELECT content FROM notes ORDER BY id DESC LIMIT 1;")
    note = cur.fetchone()
    cur.close()
    conn.close()
    
    return jsonify({
        "message": "App override is working!",
        "redis_hits": f"{float(count):.10f}", 
        "last_note": note[0] if note else "No notes yet"
    })

@app.route("/health")
def health():
    return "OK", 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)