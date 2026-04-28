from datetime import datetime

from passlib.context import CryptContext
from sqlalchemy.orm import Session

from app.models import User

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain[:72], hashed)


def hash_password(password: str) -> str:
    # bcrypt tiene un límite de 72 bytes; truncamos para evitar el ValueError
    return pwd_context.hash(password[:72])


def authenticate_user(db: Session, username: str, password: str):
    user = db.query(User).filter(User.username == username, User.is_active == True).first()
    if not user or not verify_password(password, user.hashed_password):
        return None
    user.last_login = datetime.utcnow()
    db.commit()
    return user


def create_user(db: Session, username: str, password: str, email: str = None) -> User:
    user = User(username=username, email=email, hashed_password=hash_password(password))
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def get_current_user(request, db: Session):
    username = request.session.get("username")
    if not username:
        return None
    return db.query(User).filter(User.username == username, User.is_active == True).first()


def change_password(db: Session, user: User, new_password: str):
    user.hashed_password = hash_password(new_password)
    db.commit()
