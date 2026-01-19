import sqlite3
import os

print("CWD:", os.getcwd())
conn = sqlite3.connect("local.db")
rows = conn.execute(
    "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
).fetchall()
print("TABLES:", rows)
