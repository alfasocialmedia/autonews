# Guía de configuración: Instagram Graph API en AutoNews

Permite que AutoNews publique automáticamente posts en Instagram con imagen, copy y hashtags generados por IA, cada vez que se publica una noticia en WordPress.

## Arquitectura del flujo

```
AutoNews Worker
    │
    ├── Publica en WordPress
    │       └── obtiene image_url pública (media library)
    │
    └── Publica en Instagram
            ├── Groq IA genera caption + 5 hashtags virales
            ├── Pillow genera imagen con plantilla (título + categoría)
            ├── Sube imagen a WordPress → obtiene URL pública
            └── Instagram Graph API
                    ├── POST /{ig-user-id}/media   → crea contenedor
                    └── POST /{ig-user-id}/media_publish → publica
```

---

## Requisitos previos

| Requisito | Detalle |
|-----------|---------|
| Cuenta Instagram | Debe ser **Business** o **Creator** (no funciona con personales) |
| Página de Facebook | La cuenta IG debe estar vinculada a una Fan Page |
| Cuenta Meta for Developers | Registrarse en developers.facebook.com |
| Imagen en URL pública | WordPress media library ya lo provee |

> **⚠️ Importante:** Si tu cuenta de Instagram es personal, primero conviértela a Business o Creator en la app de Instagram antes de continuar. El proceso es reversible.

---

## Paso 1 — Preparar la cuenta de Instagram

### 1.1 Convertir a cuenta Business o Creator

1. Abre la app de Instagram en el celular
2. Ir a **Perfil** → ícono de hamburguesa (≡) → **Configuración y privacidad**
3. Ir a **Tipo de cuenta y herramientas** → **Cambiar a cuenta profesional**
4. Elegir **Business** (si es una empresa/medio) o **Creator** (si es una persona pública)
5. Seguir los pasos y seleccionar una categoría (por ejemplo: "Medios de comunicación")

### 1.2 Conectar Instagram a una Página de Facebook

> **Nota:** Si no tenés una Página de Facebook, créala primero en facebook.com/pages/create. Puede ser una página nueva sin seguidores; solo necesita existir.

1. En Instagram → **Configuración** → **Centro de cuentas**
2. Hacer clic en **Agregar cuentas** y vincular tu cuenta de Facebook personal
3. Ir a Facebook → tu **Página de Facebook**
4. En la Página: **Configuración** → **Cuentas vinculadas** → **Instagram**
5. Conectar la cuenta de Instagram Business/Creator

Para verificar que la vinculación es correcta:
```
Facebook Page → Configuración → Instagram → debe mostrar el nombre de usuario de tu cuenta IG
```

---

## Paso 2 — Crear la App en Meta for Developers

### 2.1 Registrarse

1. Ir a [developers.facebook.com](https://developers.facebook.com)
2. Iniciar sesión con la cuenta de Facebook que administra la Página
3. Aceptar los términos de desarrollador si es la primera vez

### 2.2 Crear nueva App

1. Ir a **My Apps** → **Create App**
2. Seleccionar el tipo: **Business** (si no está disponible, elegir **Other** → **Business**)
3. Completar los campos:

| Campo | Valor sugerido |
|-------|----------------|
| App name | `AutoNews Bot` |
| App contact email | Tu email |
| Business account | Seleccionar tu Business Manager (o crear uno) |

4. Hacer clic en **Create App**

### 2.3 Agregar el producto Instagram Graph API

1. En el dashboard de la App → sección **Add Products to Your App**
2. Buscar **Instagram** → hacer clic en **Set Up**
3. En el menú lateral aparecerá **Instagram** → ir a **API Setup with token generator**

### 2.4 Configurar permisos mínimos necesarios

En la sección **Permissions** (o durante el proceso de revisión de la app) asegurarse de que estén habilitados:

| Permiso | Para qué sirve |
|---------|----------------|
| `instagram_content_publish` | Publicar posts, reels y stories |
| `pages_read_engagement` | Leer información de la Página de Facebook vinculada |
| `instagram_basic` | Leer información básica de la cuenta IG |

> **Nota:** Para desarrollo y pruebas, estos permisos funcionan con el token de usuario sin aprobación de Meta. Para una app en producción con múltiples cuentas ajenas se necesitaría revisión de Meta, pero para uso propio (tu propia cuenta) no hace falta.

---

## Paso 3 — Obtener el Access Token

### 3.1 Generar el User Token (corto plazo — 1 hora)

1. En el dashboard de la App → **Tools** → **Graph API Explorer**
2. En la parte superior derecha, seleccionar tu App en el dropdown
3. Hacer clic en **Generate Access Token**
4. Se abrirá un popup de Facebook — seleccionar la Página de Facebook vinculada
5. Autorizar los permisos: `instagram_content_publish`, `pages_read_engagement`, `instagram_basic`
6. Copiar el token generado (es el User Token de corto plazo)

### 3.2 Convertir a token de larga duración (60 días)

Ejecutar este comando reemplazando los valores:

```bash
curl -s "https://graph.facebook.com/v19.0/oauth/access_token
  ?grant_type=fb_exchange_token
  &client_id=TU_APP_ID
  &client_secret=TU_APP_SECRET
  &fb_exchange_token=TOKEN_CORTO_PLAZO" | python3 -m json.tool
```

Donde encontrar cada valor:
- `TU_APP_ID` → Dashboard de tu App → **App ID**
- `TU_APP_SECRET` → Dashboard → **Settings** → **Basic** → **App Secret** (hacer clic en "Show")
- `TOKEN_CORTO_PLAZO` → El token copiado en el paso anterior

Respuesta esperada:
```json
{
  "access_token": "EAAxxxxxxxxxxxxxxxxxxxxxxxx...",
  "token_type": "bearer",
  "expires_in": 5183944
}
```

> **⚠️ Importante:** Guardar el valor de `access_token`. Este es el token de larga duración (~60 días). AutoNews lo usará para publicar y lo renovará automáticamente antes de que expire.

### 3.3 Obtener el Instagram Business Account ID

Con el token de larga duración, ejecutar:

```bash
curl -s "https://graph.facebook.com/v19.0/me/accounts?access_token=TU_LONG_TOKEN" \
  | python3 -m json.tool
```

Respuesta esperada:
```json
{
  "data": [
    {
      "access_token": "EAAxxxxxxx...",
      "category": "Media",
      "name": "Mi Página de Facebook",
      "id": "123456789012345",
      "tasks": ["ANALYZE", "ADVERTISE", "MODERATE", "CREATE_CONTENT"]
    }
  ]
}
```

Tomar el `id` de la Página (por ejemplo `123456789012345`) y ejecutar:

```bash
curl -s "https://graph.facebook.com/v19.0/123456789012345?fields=instagram_business_account&access_token=TU_LONG_TOKEN" \
  | python3 -m json.tool
```

Respuesta esperada:
```json
{
  "instagram_business_account": {
    "id": "17841400000000000"
  },
  "id": "123456789012345"
}
```

El valor `instagram_business_account.id` (por ejemplo `17841400000000000`) es el **Instagram Business Account ID**. Guardarlo junto con el token.

### 3.4 Verificar que el token funciona correctamente

```bash
curl -s "https://graph.facebook.com/v19.0/17841400000000000?fields=name,username&access_token=TU_LONG_TOKEN" \
  | python3 -m json.tool
```

Respuesta esperada:
```json
{
  "name": "Mi Medio",
  "username": "mimedio",
  "id": "17841400000000000"
}
```

Si devuelve el nombre y username de tu cuenta de Instagram, el token y el ID son correctos.

---

## Paso 4 — Renovación automática del token

El token de larga duración dura **60 días**. AutoNews lo renovará automáticamente cuando queden menos de 15 días para que expire, usando este endpoint de Meta:

```bash
# AutoNews ejecuta esto automáticamente — solo como referencia
curl -s "https://graph.facebook.com/v19.0/oauth/access_token
  ?grant_type=fb_exchange_token
  &client_id=TU_APP_ID
  &client_secret=TU_APP_SECRET
  &fb_exchange_token=TOKEN_ACTUAL"
```

> **Nota:** Si el token expira por inactividad o error antes de renovarse, habrá que repetir el Paso 3.1 y 3.2 manualmente para generar uno nuevo.

---

## Paso 5 — Configurar en el panel de AutoNews

> **Nota:** Esta sección corresponde a la funcionalidad una vez implementada en el panel. Los campos a completar serán los siguientes.

1. Ir al panel → **Configuración → Instagram**
2. Completar los campos:

| Campo | Valor | Dónde se obtuvo |
|-------|-------|-----------------|
| Access Token | `EAAxxxxxxxx...` | Paso 3.2 |
| Instagram Business Account ID | `17841400000000000` | Paso 3.3 |
| App ID | ID de tu App en Meta | Dashboard de la App |
| App Secret | Secret de tu App en Meta | Settings → Basic |
| Publicación automática | Activado/Desactivado | — |
| Máx. posts por día | 25 (límite de Meta) | — |

3. Hacer clic en **Probar conexión** — debe devolver el nombre y usuario de la cuenta IG
4. Hacer clic en **Guardar**

---

## Referencia técnica

### Endpoints de la API usados

| Endpoint | Método | Descripción |
|----------|--------|-------------|
| `/{ig-user-id}/media` | POST | Crea contenedor de imagen (paso 1 de publicación) |
| `/{ig-user-id}/media_publish` | POST | Publica el contenedor (paso 2 de publicación) |
| `/oauth/access_token` | GET | Renueva el token de acceso |
| `/me/accounts` | GET | Lista las Páginas de Facebook y sus tokens |
| `/{page-id}?fields=instagram_business_account` | GET | Obtiene el ID de la cuenta IG vinculada |

### Parámetros del POST de publicación

```json
POST https://graph.facebook.com/v19.0/{ig-user-id}/media

{
  "image_url": "https://tusitio.com/wp-content/uploads/imagen.jpg",
  "caption": "Título de la noticia\n\n#hashtag1 #hashtag2 #hashtag3 #hashtag4 #hashtag5",
  "access_token": "TU_LONG_TOKEN"
}
```

```json
POST https://graph.facebook.com/v19.0/{ig-user-id}/media_publish

{
  "creation_id": "ID_DEVUELTO_POR_MEDIA",
  "access_token": "TU_LONG_TOKEN"
}
```

### Formatos de imagen soportados por Instagram

| Formato | Relación de aspecto | Resolución recomendada | Uso |
|---------|---------------------|----------------------|-----|
| Cuadrado | 1:1 | 1080 × 1080 px | Feed estándar |
| Vertical (retrato) | 4:5 | 1080 × 1350 px | Feed con más presencia visual |
| Horizontal | 1.91:1 | 1080 × 566 px | Noticias con banner ancho |

AutoNews generará imágenes en formato **4:5 (1080×1350)** por defecto, que tiene mayor visibilidad en el feed.

### Límites de la plataforma Meta

| Límite | Valor |
|--------|-------|
| Posts automáticos por día | 25 |
| Tamaño máximo de imagen | 8 MB |
| Longitud máxima del caption | 2.200 caracteres |
| Hashtags por post | 30 (se usarán 5) |
| Requests a la Graph API | 200 por hora por token |

### Archivos relevantes en AutoNews (cuando esté implementado)

| Archivo | Descripción |
|---------|-------------|
| `app/services/instagram_service.py` | Lógica de publicación en Instagram Graph API |
| `app/services/image_template_service.py` | Generación de imagen con Pillow (plantilla + título + categoría) |
| `app/routes/instagram.py` | Rutas del panel de configuración |
| `app/models.py` → `InstagramSettings` | Modelo de base de datos para las credenciales |
| `app/worker.py` | Integración en el pipeline de publicación |

---

## Solución de problemas frecuentes

### Error: `OAuthException: Invalid OAuth access token`

El token expiró o fue revocado. Repetir el Paso 3.1 y 3.2 para generar uno nuevo.

```bash
# Verificar cuándo expira el token actual:
curl -s "https://graph.facebook.com/debug_token
  ?input_token=TU_TOKEN
  &access_token=TU_APP_ID|TU_APP_SECRET" | python3 -m json.tool
# El campo "expires_at" muestra el timestamp de expiración
```

### Error: `OAuthException: (#200) Permissions error`

El token no tiene los permisos necesarios. Volver al Paso 3.1 y asegurarse de autorizar `instagram_content_publish` y `pages_read_engagement` cuando se genera el token.

### Error: `GraphMethodException: Unsupported post request`

El Instagram Business Account ID es incorrecto o la cuenta no está correctamente vinculada a la Página de Facebook. Verificar el Paso 1.2 y repetir el Paso 3.3.

### Error: `Invalid image` o `aspect_ratio_mismatch`

La imagen no cumple las proporciones aceptadas por Instagram (ver tabla de formatos). Verificar que la imagen generada sea cuadrada (1:1), vertical (4:5) u horizontal (1.91:1). No se aceptan proporciones arbitrarias.

### Error: `Account is not a Business or Creator account`

La cuenta de Instagram es personal. Seguir el Paso 1.1 para convertirla a Business o Creator.

### El token se renueva pero Instagram rechaza el nuevo token

Asegurarse de que el `App ID` y `App Secret` almacenados en AutoNews corresponden exactamente a la App que generó el token original. Si se borraron y recrearon las credenciales en Meta, hay que generar un token nuevo desde cero (Paso 3).

### La cuenta de Instagram no aparece al vincular con Facebook

La cuenta IG debe estar en modo Business/Creator **antes** de intentar vincularla a la Página de Facebook. Si se intenta vincular una cuenta personal, Facebook no la muestra como opción disponible.
