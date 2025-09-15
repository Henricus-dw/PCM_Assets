# auth.py
from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session
from database import SessionLocal
from models import User

# --- DB dependency (same pattern you use in main.py) ---


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# --- Current user helper ---


def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    # 1) Look up session user_id
    user_id = request.session.get("user_id")
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated"
        )

    # 2) Fetch user from DB
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found"
        )

    return user
