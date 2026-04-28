# AutoNews — Sistema automático de noticias

Panel de administración web + worker automático que monitorea cuentas de correo IMAP,
procesa el contenido con Groq IA y publica borradores en WordPress.

## Arquitectura

```
auto-news/
├── app/
│   ├── main.py              ← FastAPI: panel web
│   ├── worker.py            ← Worker: revisa correos cada 60s
│   ├── database.py          ← Conexión SQLAlchemy
│   ├── models.py            ← Modelos de base de datos
│   ├── auth.py              ← Autenticación y sesiones
│   ├── crypto.py            ← Cifrado de campos sensibles (Fernet)
│   ├── services/
│   │   ├── email_service.py     ← Lectura IMAP
│   │   ├── groq_service.py      ← Redacción con IA
│   │   └── wordpress_service.py ← Publicación en WordPress REST API
│   ├── routes/
│   │   ├── auth.py          ← Login/logout/perfil
│   │   ├── dashboard.py     ← Estadísticas
│   │   ├── settings.py      ← Configuración email/WP/Groq
│   │   └── posts.py         ← Bandeja de noticias
│   ├── templates/           ← Jinja2 + Bootstrap 5
│   └── static/              ← CSS y JS propios
├── deploy/
│   ├── autonews-web.service     ← systemd panel web
│   ├── autonews-worker.service  ← systemd worker
│   └── nginx.conf               ← Nginx reverse proxy
├── scripts/
│   └── init_db.py           ← Inicialización de BD
├── requirements.txt
└── .env.example
```

## Tecnologías

| Capa | Tecnología |
|------|-----------|
| Web framework | FastAPI + Uvicorn |
| Templates | Jinja2 + Bootstrap 5 |
| Base de datos | SQLite + SQLAlchemy |
| Cifrado | Fernet (cryptography) |
| Sesiones | Starlette SessionMiddleware |
| Contraseñas | bcrypt (passlib) |
| Email | imaplib (stdlib) |
| IA | Groq API (llama-3.3-70b) |
| WordPress | REST API + Application Passwords |
| Scheduler | schedule |

---

## Instalación en VPS (Ubuntu/Debian)

### 1. Preparar el servidor

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install python3 python3-pip python3-venv nginx certbot python3-certbot-nginx -y
```

### 2. Subir el proyecto

```bash
sudo mkdir -p /opt/autonews
sudo chown $USER:$USER /opt/autonews

# Copiar archivos al servidor (desde tu máquina local):
scp -r ./auto-news/* usuario@tu-vps:/opt/autonews/
```

### 3. Crear el entorno virtual e instalar dependencias

```bash
cd /opt/autonews
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

### 4. Configurar variables de entorno

```bash
cp .env.example .env
nano .env
```

Completa estos valores obligatorios:

```env
# Genera SECRET_KEY con:
# python3 -c "import secrets; print(secrets.token_hex(32))"
SECRET_KEY=tu-clave-secreta-larga

# Genera ENCRYPTION_KEY con:
# python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
ENCRYPTION_KEY=tu-fernet-key

ADMIN_PASSWORD=tu-contraseña-admin-segura
DATABASE_URL=sqlite:////opt/autonews/autonews.db
```

### 5. Inicializar la base de datos

```bash
source venv/bin/activate
python scripts/init_db.py
```

### 6. Configurar systemd

```bash
# Copiar servicios
sudo cp deploy/autonews-web.service /etc/systemd/system/
sudo cp deploy/autonews-worker.service /etc/systemd/system/

# Recargar y habilitar
sudo systemctl daemon-reload
sudo systemctl enable autonews-web autonews-worker
sudo systemctl start autonews-web autonews-worker

# Verificar estado
sudo systemctl status autonews-web
sudo systemctl status autonews-worker
```

### 7. Configurar Nginx

```bash
sudo cp deploy/nginx.conf /etc/nginx/sites-available/autonews
sudo ln -s /etc/nginx/sites-available/autonews /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl reload nginx
```

### 8. SSL con Certbot

```bash
sudo certbot --nginx -d autonews.downpro.online

# Certbot modifica automáticamente nginx.conf con la configuración SSL.
# Después, edita /etc/nginx/sites-available/autonews para asegurarte
# de que el bloque de redireccionamiento HTTP→HTTPS esté activo.
```

### 9. Permisos de archivos

```bash
sudo chown -R www-data:www-data /opt/autonews
sudo chmod 640 /opt/autonews/.env
```

---

## Uso del panel

### Acceder

Abre tu navegador en `http://autonews.downpro.online` (o `http://IP:8000` para pruebas locales).

**Credenciales por defecto:** `admin` / `admin123` — cámbialas inmediatamente en **Mi perfil**.

### Flujo de configuración inicial

1. **Correo IMAP** → Agrega tu cuenta de correo  
   - Para Gmail: activa IMAP + genera una App Password en tu cuenta Google  
2. **WordPress** → Introduce la URL, usuario y Application Password  
   - Genera el password en: WordPress → Usuarios → Tu perfil → Application Passwords  
   - Prueba la conexión y carga las categorías  
3. **Groq IA** → Introduce tu API Key de [console.groq.com](https://console.groq.com)  
   - Ajusta el prompt si lo necesitas  
   - Prueba la conexión  

Una vez configurado todo, el **worker** revisará el correo automáticamente cada 60 segundos.

### Ver logs en tiempo real

```bash
# Worker
sudo journalctl -u autonews-worker -f

# Panel web
sudo journalctl -u autonews-web -f
```

---

## Desarrollo local

```bash
# Clonar / copiar el proyecto
cd auto-news

# Crear entorno y activar
python -m venv venv
source venv/bin/activate        # Linux/Mac
# venv\Scripts\activate         # Windows

# Instalar dependencias
pip install -r requirements.txt

# Configurar .env
cp .env.example .env
# Editar .env con tus valores

# Inicializar BD
python scripts/init_db.py

# Arrancar panel (con recarga automática)
uvicorn app.main:app --reload --port 8000

# En otra terminal, arrancar el worker
python -m app.worker
```

---

## Seguridad

- Las contraseñas de correo, API keys y application passwords se almacenan **cifradas** en la base de datos con Fernet (AES-128-CBC).
- La clave de cifrado (`ENCRYPTION_KEY`) vive solo en el `.env` y **nunca** se guarda en BD.
- Las contraseñas de usuarios se almacenan como hash **bcrypt**.
- Las sesiones se firman con `itsdangerous` usando `SECRET_KEY`.
- El panel no expone nunca claves completas en pantalla (solo los primeros/últimos caracteres).

---

## Migraciones (futuro)

La primera versión usa SQLite. Para migrar a PostgreSQL:

1. Cambia `DATABASE_URL` en `.env`:
   ```
   DATABASE_URL=postgresql://usuario:contraseña@localhost/autonews
   ```
2. Instala el driver: `pip install psycopg2-binary`
3. Re-ejecuta `python scripts/init_db.py`

---

## Variables de entorno

| Variable | Descripción | Obligatoria |
|----------|-------------|-------------|
| `SECRET_KEY` | Clave para firmar sesiones | Sí |
| `ENCRYPTION_KEY` | Clave Fernet para cifrar datos sensibles | Sí |
| `ADMIN_PASSWORD` | Contraseña del usuario admin inicial | Sí (primera vez) |
| `DATABASE_URL` | URL de conexión SQLAlchemy | No (SQLite por defecto) |
| `HOST` | Dirección de escucha | No (0.0.0.0) |
| `PORT` | Puerto del servidor | No (8000) |
