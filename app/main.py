
import os

import psycopg
from flask import Flask, jsonify, request
from prometheus_client import Counter, generate_latest, CONTENT_TYPE_LATEST

app = Flask(__name__)

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://drt_user:drt_password@127.0.0.1:5434/disaster_recovery"
)

RECORDS_CREATED = Counter(
    "drt_records_created_total",
    "Total number of recovery test records created"
)


def get_connection():
    return psycopg.connect(DATABASE_URL, connect_timeout=5)


def initialize_database():
    with get_connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS recovery_records (
                id SERIAL PRIMARY KEY,
                message TEXT NOT NULL,
                created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
            )
        """)


@app.route("/")
def home():
    return jsonify({
        "project": "Automated Disaster Recovery Testing",
        "status": "running"
    })


@app.route("/health")
def health():
    try:
        with get_connection() as conn:
            conn.execute("SELECT 1")

        return jsonify({
            "status": "healthy",
            "database": "connected"
        }), 200

    except Exception:
        return jsonify({
            "status": "unhealthy",
            "database": "disconnected"
        }), 503


@app.route("/records", methods=["POST"])
def create_record():
    data = request.get_json(silent=True) or {}
    message = data.get("message", "Recovery test record")

    if not isinstance(message, str) or not message.strip():
        return jsonify({"error": "Message must be a non-empty string"}), 400

    try:
        with get_connection() as conn:
            row = conn.execute(
                """
                INSERT INTO recovery_records (message)
                VALUES (%s)
                RETURNING id, message, created_at
                """,
                (message.strip(),)
            ).fetchone()

        RECORDS_CREATED.inc()

        return jsonify({
            "id": row[0],
            "message": row[1],
            "created_at": row[2].isoformat(),
            "status": "created"
        }), 201

    except psycopg.Error:
        return jsonify({
            "error": "Database unavailable; record was not created"
        }), 503


@app.route("/records", methods=["GET"])
def get_records():
    try:
        with get_connection() as conn:
            rows = conn.execute(
                """
                SELECT id, message, created_at
                FROM recovery_records
                ORDER BY id
                """
            ).fetchall()

        return jsonify([
            {
                "id": row[0],
                "message": row[1],
                "created_at": row[2].isoformat()
            }
            for row in rows
        ]), 200

    except psycopg.Error:
        return jsonify({
            "error": "Database unavailable; records could not be retrieved"
        }), 503


@app.route("/metrics")
def metrics():
    return generate_latest(), 200, {
        "Content-Type": CONTENT_TYPE_LATEST
    }


if __name__ == "__main__":
    try:
        initialize_database()
        print("Database initialized successfully.")
    except psycopg.Error as error:
        print(f"Could not initialize database: {error}")
        raise SystemExit(1)

    app.run(host="0.0.0.0", port=5000, debug=False)
