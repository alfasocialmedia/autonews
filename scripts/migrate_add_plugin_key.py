"""
Migración: agrega la columna plugin_api_key a la tabla wordpress_settings.
Ejecutar una sola vez en el VPS:
    python scripts/migrate_add_plugin_key.py
"""
import sqlite3
import os

DB_PATH = os.environ.get("DATABASE_URL", "autonews.db").replace("sqlite:///", "")

conn = sqlite3.connect(DB_PATH)
cur = conn.cursor()

cur.execute("PRAGMA table_info(wordpress_settings)")
cols = [row[1] for row in cur.fetchall()]

if "plugin_api_key" not in cols:
    cur.execute("ALTER TABLE wordpress_settings ADD COLUMN plugin_api_key TEXT")
    conn.commit()
    print("✅ Columna plugin_api_key agregada a wordpress_settings.")
else:
    print("ℹ️  La columna plugin_api_key ya existe — sin cambios.")

conn.close()
