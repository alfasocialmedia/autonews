"""
Script de inicialización de la base de datos.
Ejecutar una sola vez antes de arrancar el sistema.

Uso:
    python scripts/init_db.py
    python scripts/init_db.py --reset   # Borra y recrea todo (¡CUIDADO!)
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from app.database import engine, SessionLocal
from app.models import Base
from app.auth import create_user, hash_password
from app.models import User


def init():
    reset = "--reset" in sys.argv

    if reset:
        print("⚠  Eliminando tablas existentes...")
        Base.metadata.drop_all(bind=engine)

    print("✅ Creando tablas...")
    Base.metadata.create_all(bind=engine)

    db = SessionLocal()
    try:
        if db.query(User).count() == 0:
            admin_pwd = os.getenv("ADMIN_PASSWORD", "admin123")
            create_user(db, "admin", admin_pwd, "admin@localhost")
            print(f"✅ Usuario 'admin' creado con contraseña: {admin_pwd}")
            if admin_pwd == "admin123":
                print("⚠  ¡AVISO! Cambia la contraseña por defecto en el panel.")
        else:
            print("ℹ  Ya existen usuarios en la base de datos. Sin cambios.")
    finally:
        db.close()

    print("\n🎉 Base de datos lista.")
    print("   Arranca el panel con: uvicorn app.main:app --reload")
    print("   Arranca el worker con: python -m app.worker")


if __name__ == "__main__":
    init()
