import sqlite3
import json
from datetime import datetime

DB_PATH = "search_history.db"

def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS searches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT,
            origin TEXT,
            destination TEXT,
            departure_date TEXT,
            cabin TEXT,
            points_input TEXT,
            best_cash_price REAL,
            best_award_program TEXT,
            best_award_points INTEGER,
            best_award_taxes REAL,
            cpp_achieved REAL,
            recommendation TEXT
        )
    """)
    conn.commit()
    conn.close()

def log_search(origin, destination, departure_date, cabin,
               points_input, result_text):
    init_db()
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        INSERT INTO searches
        (timestamp, origin, destination, departure_date, cabin,
         points_input, recommendation)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (
        datetime.now().isoformat(),
        origin.upper(),
        destination.upper(),
        str(departure_date),
        cabin,
        points_input,
        result_text[:500]
    ))
    conn.commit()
    conn.close()

def get_history():
    init_db()
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute("""
        SELECT timestamp, origin, destination, departure_date,
               cabin, points_input, recommendation
        FROM searches
        ORDER BY timestamp DESC
        LIMIT 50
    """).fetchall()
    conn.close()
    return rows
