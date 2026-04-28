import os
from cryptography.fernet import Fernet


def _fernet() -> Fernet:
    key = os.getenv("ENCRYPTION_KEY", "")
    if not key:
        raise RuntimeError(
            "ENCRYPTION_KEY no está definida. "
            "Genera una con: python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
        )
    return Fernet(key.encode() if isinstance(key, str) else key)


def encrypt_value(value: str) -> str:
    return _fernet().encrypt(value.encode()).decode()


def decrypt_value(encrypted: str) -> str:
    return _fernet().decrypt(encrypted.encode()).decode()


def mask_value(value: str, visible: int = 4) -> str:
    """Devuelve solo los primeros y últimos `visible` caracteres."""
    if not value or len(value) <= visible * 2:
        return "****"
    return value[:visible] + "..." + value[-visible:]


def generate_key() -> str:
    return Fernet.generate_key().decode()
