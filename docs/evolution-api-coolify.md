# Guía de instalación y configuración: Evolution API v2 en Coolify

## Arquitectura del servidor

El VPS utiliza **nginx nativo** (no Docker) como proxy inverso en el puerto 443, gestionado por Certbot/Let's Encrypt. Coolify despliega los servicios en contenedores Docker internamente, pero el tráfico externo llega primero a nginx antes que a Traefik o Caddy de Coolify.

```
Internet
    │
    ▼
nginx (puerto 443, nativo)
    ├── newsbot.downpro.online  → 127.0.0.1:3000  → contenedor autonews
    └── evolution.downpro.online → 127.0.0.1:8181 → contenedor Evolution API
```

Esta arquitectura implica que **las labels de Traefik en los contenedores Docker son ignoradas externamente**. Todo servicio que se quiera exponer requiere:
1. Mapeo de puerto en el docker-compose
2. Bloque de servidor en nginx
3. Certificado SSL con Certbot

---

## Paso 1 — Instalar Evolution API en Coolify

### 1.1 Crear el servicio

1. En Coolify → **New Service** → buscar **"Evolution API"**
2. Seleccionar la plantilla oficial (`evoapicloud/evolution-api:v2.3.7`)
3. Hacer clic en **Deploy** para que Coolify genere las variables de entorno automáticamente

### 1.2 Configurar el dominio en Coolify

1. Ir a **Configuration** → **Api** → **Settings** (botón junto al servicio api)
2. En el campo **Domains** ingresar:
   ```
   https://evolution.downpro.online
   ```
   Si Coolify muestra una advertencia de puerto requerido, hacer clic en **"I understand, remove port anyway"**
3. Guardar

> **Nota:** El dominio en Coolify solo es relevante para las labels internas de Traefik. El acceso externo real se configura en nginx (ver Paso 3).

### 1.3 Editar el docker-compose

1. Ir a **Configuration** → **Edit Compose File**
2. En el servicio `api`, realizar dos cambios:

**Cambio 1 — SERVER_URL:**
```yaml
# Antes:
- 'SERVER_URL=${SERVICE_URL_EVO}'

# Después:
- 'SERVER_URL=https://evolution.downpro.online'
```

**Cambio 2 — Exponer puerto al host:**
```yaml
# Reemplazar:
expose:
  - '8080'

# Por:
ports:
  - "8181:8080"
```

3. Guardar y hacer **Redeploy**

### 1.4 Obtener la API Key

1. Ir a **Environment Variables**
2. Buscar `SERVICE_PASSWORD_AUTHENTICATIONAPIKEY`
3. Hacer clic en el ícono del ojo (👁) para revelar el valor
4. Guardar este valor — se usará en el panel de botnews

---

## Paso 2 — Abrir el puerto en el firewall

```bash
ufw allow 8181/tcp
ufw status
```

---

## Paso 3 — Configurar nginx

### 3.1 Crear el bloque de servidor

```bash
cat > /etc/nginx/sites-available/evolution << 'EOF'
server {
    server_name evolution.downpro.online;

    location / {
        proxy_pass http://127.0.0.1:8181;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_read_timeout 60s;
    }

    listen 80;
}
EOF
```

### 3.2 Activar el sitio

```bash
ln -s /etc/nginx/sites-available/evolution /etc/nginx/sites-enabled/
nginx -t && nginx -s reload
```

---

## Paso 4 — Obtener certificado SSL

```bash
certbot --nginx -d evolution.downpro.online \
  --non-interactive \
  --agree-tos \
  -m tu@email.com
```

Certbot modificará automáticamente el bloque nginx para agregar SSL. El certificado se renueva automáticamente cada 90 días.

---

## Paso 5 — Verificar que Evolution API responde

```bash
curl -s https://evolution.downpro.online | head -50
```

Debe responder con:
```json
{"status": 200, "message": "Welcome to the Evolution API..."}
```

---

## Paso 6 — Configurar en el panel de botnews

1. Ir al panel → **Configuración → WhatsApp**
2. Completar los campos de la primera cuenta:

| Campo | Valor |
|-------|-------|
| URL de Evolution API | `https://evolution.downpro.online` |
| API Key | Valor de `SERVICE_PASSWORD_AUTHENTICATIONAPIKEY` |
| Nombre de instancia | `botnews` (o el que prefieras) |

3. Hacer clic en **Guardar**
4. Hacer clic en **Crear instancia**
5. Hacer clic en **Ver QR** y escanear con WhatsApp
6. Configurar webhook con la URL pública del panel:
   ```
   https://newsbot.downpro.online
   ```

---

## Paso 7 — Agregar un segundo número de WhatsApp

El sistema soporta múltiples cuentas de WhatsApp, cada una con su propio número. Todas comparten la misma instalación de Evolution API.

### 7.1 Crear la nueva cuenta en el panel

1. Ir al panel → **Configuración → WhatsApp** → botón **Agregar cuenta**
2. Completar:

| Campo | Valor |
|-------|-------|
| Nombre descriptivo | Ej: `Canal 7` |
| WordPress destino | El sitio WordPress al que debe publicar esta cuenta |
| URL de Evolution API | La misma URL del paso 6 |
| API Key | La misma API key del paso 6 |
| **Nombre de instancia** | **Un nombre NUEVO y único** — ej: `botnews2` |

> El nombre de instancia es el único campo que debe ser diferente. La URL y API key son las mismas porque es la misma instalación de Evolution API.

3. Activar **Recepción activa** y **Difusión activa** según necesites
4. Hacer clic en **Crear cuenta**

### 7.2 Conectar el segundo número

Dentro de la nueva cuenta en el acordeón del panel:

1. Hacer clic en **Crear instancia** — Evolution API registra la instancia `botnews2`
2. Hacer clic en **Ver QR** — aparece el código QR
3. Escanearlo con el segundo número de WhatsApp
4. Verificar con el botón **Verificar** — debe aparecer "Conectado"

### 7.3 Configurar el webhook del segundo número

El webhook es la misma URL para todas las cuentas — Evolution API incluye el campo `instance` en el payload y el sistema enruta automáticamente:

1. En el campo webhook de la nueva cuenta, ingresar la URL del panel:

   ```text
   https://newsbot.downpro.online
   ```

2. Hacer clic en **Configurar**

> El endpoint `/webhook/whatsapp` es único. El sistema diferencia qué cuenta procesa cada mensaje usando el campo `instance` del payload de Evolution API.

### 7.4 Agregar grupos al segundo número

Los grupos son por cuenta — no se comparten:

1. Hacer clic en **Cargar de WA** para ver los grupos del segundo número
2. Seleccionar los grupos destino
3. Opcionalmente asignar a cada grupo un **WordPress destino** específico

---

## Referencia técnica

### Contenedores en ejecución

| Contenedor | Descripción | Puerto |
|-----------|-------------|--------|
| `api-em7li6ap7egnnq1wkb92gn58` | Evolution API | host:8181 → container:8080 |
| `postgres-em7li6ap7egnnq1wkb92gn58` | PostgreSQL de Evolution API | interno |
| `redis-em7li6ap7egnnq1wkb92gn58` | Redis de Evolution API | interno |
| `autonews` | Panel botnews | host:3000 |

### Formato del webhook en Evolution API v2

El body de la llamada al endpoint `/webhook/set/{instance}` debe estar anidado bajo la propiedad `webhook`:

```json
{
  "webhook": {
    "enabled": true,
    "url": "https://newsbot.downpro.online/webhook/whatsapp",
    "webhookByEvents": false,
    "webhookBase64": false,
    "events": ["MESSAGES_UPSERT", "CONNECTION_UPDATE"]
  }
}
```

### Archivos relevantes en botnews

| Archivo | Descripción |
|---------|-------------|
| `app/services/whatsapp_service.py` | Lógica de conexión con Evolution API |
| `app/routes/whatsapp.py` | Rutas del panel y webhook receptor |

### Nginx en el VPS

| Dominio | Archivo de configuración | Puerto destino |
|---------|-------------------------|----------------|
| `newsbot.downpro.online` | `/etc/nginx/sites-available/newsbot` | 3000 |
| `evolution.downpro.online` | `/etc/nginx/sites-available/evolution` | 8181 |

---

## Solución de problemas frecuentes

### Error: Read timed out
El código del VPS tiene un timeout viejo (20s). Verificar que `TIMEOUT = 60` en `whatsapp_service.py` y que el último commit esté desplegado.

### Error: 401 Unauthorized
La API Key ingresada no coincide con `SERVICE_PASSWORD_AUTHENTICATIONAPIKEY`. Revelar el valor en Coolify y copiarlo exactamente.

### Error: "instance requires property webhook"
El body del webhook no está anidado correctamente. Verificar que use el formato `{"webhook": {...}}` (ver sección de formato de webhook arriba).

### El dominio apunta a botnews en lugar de Evolution API
nginx tiene un único bloque SSL y lo usa como default para dominios desconocidos. Solución: agregar bloque nginx específico para `evolution.downpro.online` (Paso 3).

### Evolution API no puede conectarse a PostgreSQL tras cambios de red
No usar "Connect To Predefined Network" en Coolify para Evolution API — rompe la resolución del hostname `postgres` interno. Si se necesita red compartida, cambiar `DB_POSTGRESDB_HOST` al nombre completo del contenedor antes de habilitar la red.
