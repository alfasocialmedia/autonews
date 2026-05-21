# Guía de configuración: Instagram en AutoNews

## Índice
1. [Requisitos previos](#1-requisitos-previos)
2. [Crear la App en Meta for Developers](#2-crear-la-app-en-meta-for-developers)
3. [Obtener el Access Token](#3-obtener-el-access-token)
4. [Configurar la cuenta en AutoNews](#4-configurar-la-cuenta-en-autonews)
5. [Detectar el Instagram Business ID](#5-detectar-el-instagram-business-id)
6. [Configurar el diseño de imagen](#6-configurar-el-diseño-de-imagen)
7. [Vincular al feed RSS o cuenta IMAP](#7-vincular-al-feed-rss-o-cuenta-imap)
8. [Flujo completo de publicación](#8-flujo-completo-de-publicación)
9. [Solución de problemas frecuentes](#9-solución-de-problemas-frecuentes)
10. [Renovar el token](#10-renovar-el-token)

---

## 1. Requisitos previos

Antes de comenzar necesitás tener:

- **Cuenta de Instagram** convertida a **Business** o **Creator**
  - En la app de Instagram: Perfil → Configuración → Cuenta → Cambiar tipo de cuenta
- **Cuenta de Facebook** vinculada a esa cuenta de Instagram (para crear la App de Meta)
- **Cuenta en Meta for Developers**: [developers.facebook.com](https://developers.facebook.com)
- Un **sitio WordPress activo** en AutoNews (la imagen procesada se sube temporalmente a WP para obtener una URL pública que Instagram puede descargar)

---

## 2. Crear la App en Meta for Developers

### 2.1 Crear nueva aplicación

1. Ir a [developers.facebook.com/apps](https://developers.facebook.com/apps)
2. Clic en **Crear app**
3. Seleccionar caso de uso: **"Instagram API with Instagram Login"**
4. Completar:
   - **Nombre de la app**: cualquier nombre descriptivo (ej: "AutoNews Canal 7")
   - **Email de contacto**: tu email
5. Crear la app

### 2.2 Configurar el modo "Live"

La app en modo **Desarrollo** solo puede publicar en cuentas de prueba. Para publicar en la cuenta real:

1. En el panel de la app → **Dashboard** (arriba a la izquierda)
2. Cambiar el selector de **Development → Live**
3. Si pide verificación de negocio, hacerla (puede requerir documentos de la empresa)

> **Nota**: Podés probar en Desarrollo si agregás tu cuenta de Instagram como **Evaluador** (ver 2.3).

### 2.3 Agregar evaluadores (para modo Development)

1. En la app → **Roles** → **Evaluadores de Instagram**
2. Agregar el usuario de Instagram que va a recibir las publicaciones
3. El usuario debe aceptar la invitación desde la app de Instagram

### 2.4 Obtener App ID y App Secret

1. En el panel de la app → **Configuración** → **Básica**
2. Copiar:
   - **ID de la app** (App ID): número de 15+ dígitos
   - **Clave secreta de la app** (App Secret): hash de 32 caracteres (clic en "Mostrar" para verla)

---

## 3. Obtener el Access Token

Hay dos métodos:

### Método A: OAuth automático (recomendado)

1. En AutoNews → Configuración → Instagram → tu cuenta
2. Completar **App ID** y **App Secret** → **Guardar**
3. Clic en el botón **"Conectar con Meta"**
4. Se abre una ventana de Meta donde autorizás los permisos:
   - `instagram_business_basic`
   - `instagram_content_publish`
5. Al aceptar, el token se guarda automáticamente como **long-lived token** (~60 días)

### Método B: Token manual

1. Ir al [Graph API Explorer](https://developers.facebook.com/tools/explorer/)
2. Seleccionar tu app
3. Solicitar permisos: `instagram_basic`, `instagram_content_publish`
4. Generar token
5. Convertir a long-lived token usando:
   ```
   GET https://graph.instagram.com/access_token
     ?grant_type=ig_exchange_token
     &client_secret={APP_SECRET}
     &access_token={SHORT_TOKEN}
   ```
6. Copiar el `access_token` de la respuesta y pegarlo en AutoNews

> **Importante**: Los tokens de Instagram Login duran **60 días** y se pueden renovar antes de vencer.

---

## 4. Configurar la cuenta en AutoNews

### Ruta: Configuración → Instagram → Nueva cuenta (o editar existente)

| Campo | Descripción |
|-------|-------------|
| **Nombre descriptivo** | Nombre interno para identificar la cuenta (ej: "Canal 7 Instagram") |
| **Instagram Business Account ID** | ID numérico de la cuenta (17841XXXXXXXXXX) — ver sección 5 |
| **App ID** | El App ID de Meta obtenido en el paso 2.4 |
| **App Secret** | La clave secreta de la app (solo se guarda la primera vez) |
| **Access Token** | El token de larga duración (60 días) |
| **Máx. posts por día** | Límite de publicaciones (Meta permite hasta 25/día) |

### Acciones disponibles

- **Guardar**: guarda todas las configuraciones
- **Conectar con Meta**: inicia el flujo OAuth para obtener el token automáticamente
- **Probar conexión**: verifica que el token y el ID de cuenta sean válidos
- **Publicar prueba**: publica una imagen de prueba con el diseño configurado en tu cuenta real de Instagram
- **Renovar ahora**: renueva el token antes de que expire

---

## 5. Detectar el Instagram Business ID

El ID de cuenta de Instagram es un número (no el @usuario). Para obtenerlo:

### Opción A: Botón "Detectar" (automático)

1. Una vez guardado el Access Token → aparece el botón **Detectar**
2. El sistema consulta la API de Instagram y autodetecta el ID
3. Si hay varias cuentas vinculadas, seleccionás la correcta de la lista

### Opción B: Manual via Meta

1. En [developers.facebook.com](https://developers.facebook.com) → Graph API Explorer
2. Consultar: `GET /me?fields=id,username&access_token={TOKEN}`
3. El campo `id` que devuelve es el Instagram Business ID

### Opción C: Business Portfolio

Si la cuenta está bajo un Business Portfolio:
1. En el panel de tu app Meta buscá el Business ID en la URL (parámetro `?business_id=XXXXX`)
2. En AutoNews → botón Detectar → si aparece la opción "Business Portfolio" → ingresar el Business ID → Buscar

---

## 6. Configurar el diseño de imagen

Cada cuenta de Instagram tiene su propio diseño de imagen independiente. El diseño se aplica sobre la imagen del artículo generando una imagen de **1080 × 1440 px (4:5)** — el formato estándar de feed de Instagram.

### Secciones del panel de diseño

#### 🔲 Gradiente (borde gris)

El gradiente oscuro que cubre la parte inferior de la imagen para que el texto sea legible.

| Control | Descripción |
|---------|-------------|
| **Color** | Color base del gradiente (generalmente negro `#000000`) |
| **Intensidad** | Qué tan opaco es el gradiente (0 = invisible, 255 = sólido) |
| **Alto** | Hasta qué altura sube el gradiente en píxeles (100–1440 px) |

#### 🔵 Título (borde azul)

El texto del titular de la noticia superpuesto sobre la imagen.

| Control | Descripción |
|---------|-------------|
| **Color** | Color del texto del título |
| **Tamaño** | Tamaño de fuente en px (20–120) |
| **Fuente** | Familia tipográfica (Montserrat, Poppins, Playfair Display, Oswald, etc.) |
| **Grosor** | Peso de la fuente (Regular, Medium, Bold, Extra Bold) |
| **Alineación** | Izquierda / Centro / Derecha |
| **Posición vertical** | Desplaza el texto hacia arriba o abajo en px |
| **Máx. líneas** | Límite de líneas que puede ocupar el título (1–6) |

> Las fuentes se descargan automáticamente de Google Fonts la primera vez que se usan.

#### 🔵 Fondo del título (borde cyan)

Un rectángulo semitransparente detrás del texto del título para mejorar la legibilidad.

| Control | Descripción |
|---------|-------------|
| **Color** | Color del fondo del rectángulo |
| **Opacidad** | 0 = sin fondo, 220 = casi sólido |
| **Padding vertical** | Espacio extra arriba y abajo del texto |
| **Ancho completo** | Activo: el fondo va de borde a borde. Desactivado: se ajusta al ancho del texto |
| **Padding lateral** | (Solo cuando Ancho completo está desactivado) Espacio extra a los lados |

#### 🔴 Franja inferior (borde rojo)

Un badge/etiqueta en la parte inferior de la imagen con el nombre del medio o URL.

| Control | Descripción |
|---------|-------------|
| **Texto** | El texto que aparece (ej: "tusitio.com.ar" o "CANAL 7 NOTICIAS"). Vacío = oculta la franja |
| **Estilo** | Píldora (bordes redondeados) / Rectángulo / Sin fondo (solo texto con sombra) |
| **Grosor texto** | Peso de la fuente del texto de la franja |
| **Color fondo** | Color del badge de la franja |
| **Color texto** | Color del texto de la franja |
| **Alineación** | Izquierda / Centro / Derecha |
| **Posición vertical** | Desplaza la franja hacia arriba o abajo |

#### 🟠 Badge de categoría (borde naranja)

Un badge en la parte superior que muestra la categoría de la noticia (POLICIALES, DEPORTES, etc.) extraída automáticamente por la IA.

| Control | Descripción |
|---------|-------------|
| **Mostrar categoría** | Activa/desactiva el badge |
| **Color fondo** | Color del badge |
| **Color texto** | Color del texto del badge |
| **Posición horizontal** | Deslizá de izquierda (0%) a derecha (100%) |

> La categoría se extrae automáticamente de la noticia mediante la IA. En la vista previa aparece "CATEGORÍA EJEMPLO".

#### 🟢 Logo (borde verde)

El logo del medio superpuesto sobre la imagen.

| Control | Descripción |
|---------|-------------|
| **Archivo** | Subir el logo (PNG recomendado para transparencia, máx. recomendado: 500 KB) |
| **Posición** | Esquina donde aparece el logo |
| **Tamaño** | Tamaño máximo del logo en px (60–300) |

### Botón "Ver previa"

Genera una vista previa en tiempo real con todos los controles actuales. La previa usa una foto de muestra de Pollinations.ai — no la imagen del artículo real.

> **Tip**: Ajustá los sliders en tiempo real y la previa se actualiza automáticamente (~400 ms de debounce).

---

## 7. Vincular al feed RSS o cuenta IMAP

Para que AutoNews publique automáticamente en Instagram cuando procesa una noticia, tenés que vincular la cuenta de Instagram al feed RSS o cuenta de email IMAP correspondiente.

### Para feeds RSS

1. Ir a **Configuración → RSS Feeds** → editar el feed
2. En el campo **"Cuenta de Instagram"** → seleccionar la cuenta configurada
3. Guardar

### Para cuentas IMAP (email)

1. Ir a **Configuración → Cuentas de Email** → editar la cuenta
2. En el campo **"Cuenta de Instagram"** → seleccionar la cuenta configurada
3. Guardar

> Si una fuente **no tiene cuenta de Instagram vinculada**, AutoNews busca la primera cuenta de Instagram activa como fallback. Si querés que una fuente específica **no publique en Instagram**, no vincules ninguna cuenta Y asegurate de que la cuenta activa sea para otro medio.

---

## 8. Flujo completo de publicación

Cuando AutoNews detecta una nueva noticia, el proceso es:

```
1. [Worker] Descarga el artículo del RSS feed / email IMAP
        ↓
2. [Groq/IA] Procesa el contenido: extrae título, resumen, categoría, tags
        ↓
3. [WordPress] Publica el artículo en el sitio web
        ↓
4. [Imagen] Descarga la imagen del artículo
        ↓
5. [Pillow] Aplica el diseño configurado:
   - Redimensiona a 1080×1440 px (4:5)
   - Aplica gradiente oscuro
   - Superpone el título con la fuente y estilo configurados
   - Agrega el fondo del título (si opacidad > 0)
   - Dibuja la franja inferior (si tiene texto)
   - Dibuja el badge de categoría (si está activado)
   - Superpone el logo (si está configurado)
        ↓
6. [WordPress Media] Sube la imagen procesada como media en WP
   (para obtener una URL pública accesible por Instagram)
        ↓
7. [Groq/IA] Genera el caption de Instagram:
   - Frase gancho con emojis
   - Copy breve y directo (2-3 líneas)
   - 5 hashtags virales
   - Footer con el sitio web
        ↓
8. [Instagram Graph API] Publica en 2 pasos:
   a) Crea un container (media) con la URL de imagen + caption
   b) Espera 3 segundos y luego publica el container
        ↓
9. [Log] Registra el resultado en el panel de AutoNews
```

### Caption generado por IA

El caption de Instagram es generado automáticamente con esta estructura:

```
🔥 [Frase gancho impactante con emojis]

[Copy breve con emojis integrados — 2-3 líneas]

#hashtag1 #hashtag2 #hashtag3 #hashtag4 #hashtag5

📰 [URL del sitio web o texto de la franja inferior]
```

Si la IA no puede generar el caption (error de conexión, cuota agotada, etc.), se usa un caption genérico de respaldo.

---

## 9. Solución de problemas frecuentes

### La imagen se publica sin el diseño configurado

**Causa más probable**: el campo `instagram_settings_id` del feed RSS no está vinculado, y la cuenta de Instagram que se usa como fallback tiene configuración distinta.

**Verificar**:
1. Ir al feed RSS → confirmar que tiene la cuenta de Instagram correcta seleccionada
2. En el panel de Logs de AutoNews buscar mensajes `[IG]` para ver qué cuenta se usó

**Otras causas posibles**:
- La imagen del artículo no se pudo descargar → verificar en los Logs `[IG] Sin imagen disponible`
- Error al procesar con Pillow → verificar `[IG] Error en build_instagram_image`
- El sitio WordPress no tiene acceso a Internet para alojar la imagen pública

### El caption solo tiene texto genérico / solo el título

**Causa**: La IA (Groq) no pudo generar el caption.

**Verificar**:
1. Ir a **Configuración → IA (Groq)** → probar la conexión
2. Verificar que el API key y el proveedor estén correctamente configurados
3. Verificar que no se haya agotado la cuota del proveedor de IA

### Error "invalid_parameter" al crear el container

Instagram rechaza la imagen si:
- La URL no es HTTPS pública (el sitio WordPress debe tener SSL)
- La imagen no cumple el formato (se necesita JPEG, ratio 4:5, mínimo 500px)
- El Instagram Business ID es incorrecto

**Solución**: verificar que el ID sea el número largo (17841XXXXXXXXXX) y no el @usuario.

### Error "Media ID does not exist"

El container se creó pero expiró antes de publicarse. El sistema espera 3 segundos entre crear y publicar — si el servidor está muy lento puede fallar.

### El token expiró

Los tokens duran ~60 días. Síntoma: error `"Error Token de acceso de usuario no válido"`.

**Solución**: clic en el botón **"Renovar ahora"** en el panel de configuración de la cuenta de Instagram.

### La cuenta de Instagram no aparece como "Detectada"

1. Verificar que el token sea válido → botón **Probar conexión**
2. Verificar que la cuenta sea Business o Creator (no personal)
3. Si usás Business Portfolio → usar la opción "Buscar por Business ID"

---

## 10. Renovar el token

Los tokens de Instagram Login (largo plazo) vencen aproximadamente a los 60 días.

### Renovación automática (recomendado)

En el panel de la cuenta → el botón **"Renovar ahora"** aparece junto a la fecha de vencimiento.

El sistema también muestra la fecha de vencimiento en la sección de Estado de la cuenta.

### Renovación manual

Si el token ya venció y el botón no funciona:
1. Repetir el proceso de OAuth → clic en **"Conectar con Meta"**
2. Se obtiene un nuevo token y se actualiza automáticamente

> **Tip**: Configurar un recordatorio para renovar el token 1 semana antes de la fecha de vencimiento mostrada en el panel.

---

## Notas técnicas

| Parámetro | Valor |
|-----------|-------|
| Formato de imagen generada | JPEG, 1080 × 1440 px, calidad 90% |
| Ratio de imagen | 4:5 (el estándar de feed de Instagram) |
| Duración del token | ~60 días (renovable) |
| Límite de publicaciones | 25 posts por día (límite de Meta) |
| Caption máximo | 2200 caracteres |
| Tiempo entre container y publicación | 3 segundos (requerido por Meta API) |
| Endpoint API usado | `graph.instagram.com` (Instagram API with Instagram Login) |
