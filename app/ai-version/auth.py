import os
import redis
from fastapi import APIRouter, Header, HTTPException, Request, status
from pydantic import BaseModel
from supabase import create_client

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")
r = redis.from_url(REDIS_URL, decode_responses=True)

RATE_LIMIT_MAX = 5
RATE_LIMIT_WINDOW = 300  # seconds

router = APIRouter(prefix="/auth", tags=["auth"])


class Credentials(BaseModel):
    email: str
    password: str


def check_rate_limit(request: Request, email: str):
    key = f"login_attempts:{request.client.host}:{email}"
    attempts = r.incr(key)
    if attempts == 1:
        r.expire(key, RATE_LIMIT_WINDOW)
    if attempts > RATE_LIMIT_MAX:
        return {
            "success": False,
            "flag": "RATE_LIMITED",
            "message": "Too many login attempts, try again later",
        }
    return None


@router.post("/signup")
def signup(creds: Credentials):
    result = supabase.auth.sign_up({"email": creds.email, "password": creds.password})
    return {"success": True, "user": result.user}


@router.post("/login")
def login(creds: Credentials, request: Request):
    limited = check_rate_limit(request, creds.email)
    if limited:
        return limited

    result = supabase.auth.sign_in_with_password(
        {"email": creds.email, "password": creds.password}
    )
    if result.session is None:
        return {"success": False, "flag": "INVALID_CREDENTIALS", "message": "Login failed"}

    return {
        "success": True,
        "access_token": result.session.access_token,
        "refresh_token": result.session.refresh_token,
    }


def get_token_from_header(authorization: str = Header(None)):
    token = authorization.split(" ")[1]
    return token


@router.post("/logout")
def logout(authorization: str = Header(None)):
    token = get_token_from_header(authorization)
    supabase.auth.sign_out()
    return {"success": True, "flag": "LOGGED_OUT"}