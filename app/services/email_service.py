from __future__ import annotations

import email
import imaplib
import re
from datetime import datetime
from email.header import decode_header
from email.utils import parsedate_to_datetime


def _decode_str(s: str) -> str:
    if not s:
        return ""
    parts = decode_header(s)
    result = []
    for chunk, enc in parts:
        if isinstance(chunk, bytes):
            result.append(chunk.decode(enc or "utf-8", errors="replace"))
        else:
            result.append(str(chunk))
    return "".join(result)


def _get_body(msg) -> str:
    """Extrae texto plano del mensaje; si no hay, cae a HTML."""
    body = ""
    if msg.is_multipart():
        for part in msg.walk():
            ct = part.get_content_type()
            disp = str(part.get("Content-Disposition", ""))
            if "attachment" in disp:
                continue
            if ct == "text/plain":
                charset = part.get_content_charset() or "utf-8"
                body = (part.get_payload(decode=True) or b"").decode(charset, errors="replace")
                break
            if ct == "text/html" and not body:
                charset = part.get_content_charset() or "utf-8"
                body = (part.get_payload(decode=True) or b"").decode(charset, errors="replace")
    else:
        charset = msg.get_content_charset() or "utf-8"
        body = (msg.get_payload(decode=True) or b"").decode(charset, errors="replace")

    # Strip basic HTML tags for plain-text storage
    body = re.sub(r"<[^>]+>", " ", body)
    body = re.sub(r"\s+", " ", body)
    return body.strip()


def test_imap_connection(server: str, port: int, username: str, password: str) -> tuple[bool, str]:
    try:
        mail = imaplib.IMAP4_SSL(server, int(port))
        mail.login(username, password)
        mail.logout()
        return True, "Conexión IMAP exitosa"
    except Exception as exc:
        return False, str(exc)


def _get_image_attachment(msg) -> tuple[bytes | None, str, str]:
    """Extrae el primer adjunto de imagen del mensaje."""
    for part in msg.walk():
        ct = part.get_content_type()
        if not ct.startswith("image/"):
            continue
        payload = part.get_payload(decode=True)
        if not payload:
            continue
        raw_filename = part.get_filename() or f"image.{ct.split('/')[-1]}"
        filename = _decode_str(raw_filename)
        return payload, filename, ct
    return None, "", ""


def _extract_image_url_from_body(msg) -> str | None:
    """Extrae la primera URL de imagen usable del cuerpo del correo (HTML o texto).

    Prioridad:
    1. Google Drive carpeta  → devuelve "gdrive-folder:{FOLDER_ID}" (resuelto en worker)
    2. Google Drive archivo  → convierte a URL de descarga directa
    3. <img src="..."> en HTML
    4. URL directa de imagen en texto plano
    """
    html_body = ""
    text_body = ""

    if msg.is_multipart():
        for part in msg.walk():
            ct = part.get_content_type()
            disp = str(part.get("Content-Disposition", ""))
            if "attachment" in disp:
                continue
            if ct == "text/html" and not html_body:
                charset = part.get_content_charset() or "utf-8"
                html_body = (part.get_payload(decode=True) or b"").decode(charset, errors="replace")
            elif ct == "text/plain" and not text_body:
                charset = part.get_content_charset() or "utf-8"
                text_body = (part.get_payload(decode=True) or b"").decode(charset, errors="replace")
    else:
        ct = msg.get_content_type()
        charset = msg.get_content_charset() or "utf-8"
        raw = (msg.get_payload(decode=True) or b"").decode(charset, errors="replace")
        if ct == "text/html":
            html_body = raw
        else:
            text_body = raw

    content = html_body or text_body
    if not content:
        return None

    # 1. Google Drive — carpeta (necesita API key para listar archivos)
    gdrive_folder = re.search(
        r'https?://drive\.google\.com/drive/folders/([a-zA-Z0-9_-]+)',
        content,
    )
    if gdrive_folder:
        return f"gdrive-folder:{gdrive_folder.group(1)}"

    # 2. Google Drive — archivo directo (file/d/ o open?id=)
    gdrive_file = re.search(
        r'https?://drive\.google\.com/(?:file/d/|open\?id=)([a-zA-Z0-9_-]+)',
        content,
    )
    if gdrive_file:
        file_id = gdrive_file.group(1)
        return f"https://drive.google.com/uc?export=download&id={file_id}"

    # 3. <img src="..."> en HTML (saltar cid: y data:)
    for m in re.finditer(r'<img[^>]+src=["\']([^"\']+)["\']', content, re.IGNORECASE):
        url = m.group(1)
        if url.startswith(("http://", "https://")):
            return url

    # 4. URL directa de imagen en texto plano
    m = re.search(
        r'https?://[^\s<>"\']+\.(?:jpg|jpeg|png|gif|webp)(?:[?#][^\s<>"\']*)?',
        content,
        re.IGNORECASE,
    )
    if m:
        return m.group(0)

    return None


def fetch_unread_emails(server: str, port: int, username: str, password: str) -> list[dict]:
    results = []
    mail = imaplib.IMAP4_SSL(server, int(port))
    try:
        mail.login(username, password)
        mail.select("INBOX")

        _, data = mail.search(None, "UNSEEN")
        ids = data[0].split() if data[0] else []

        for eid in ids:
            _, msg_data = mail.fetch(eid, "(RFC822)")
            raw = msg_data[0][1]
            msg = email.message_from_bytes(raw)

            message_id = msg.get("Message-ID", f"<local-{eid.decode()}>").strip()
            sender = _decode_str(msg.get("From", ""))
            subject = _decode_str(msg.get("Subject", "(sin asunto)"))
            date_str = msg.get("Date", "")

            try:
                received_at = parsedate_to_datetime(date_str)
            except Exception:
                received_at = datetime.utcnow()

            body = _get_body(msg)
            image_data, image_filename, image_mime = _get_image_attachment(msg)
            image_url = None if image_data else _extract_image_url_from_body(msg)

            # Marcar como leído
            mail.store(eid, "+FLAGS", "\\Seen")

            results.append(
                {
                    "message_id": message_id,
                    "sender": sender,
                    "subject": subject,
                    "body": body,
                    "received_at": received_at,
                    "image_data": image_data,
                    "image_filename": image_filename,
                    "image_mime": image_mime,
                    "image_url": image_url,
                }
            )
    finally:
        try:
            mail.logout()
        except Exception:
            pass

    return results
