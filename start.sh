#!/bin/bash
set -e

# El worker se inicia dentro de main.py (start_background) después de crear las tablas.
# No lanzar un proceso worker separado para evitar conflictos de escritura en SQLite.

exec uvicorn app.main:app --host 0.0.0.0 --port 3000
