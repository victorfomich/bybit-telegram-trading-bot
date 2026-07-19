from itsdangerous import URLSafeTimedSerializer
from fastapi import Request
from fastapi.responses import JSONResponse, RedirectResponse

from app.config import settings

COOKIE_NAME = "site_auth"
AUTH_MAX_AGE = 60 * 60 * 24 * 30  # 30 days

_serializer = URLSafeTimedSerializer(settings.site_auth_secret, salt="site-login")


def make_auth_token() -> str:
    return _serializer.dumps({"ok": True})


def verify_auth_token(token: str | None) -> bool:
    if not token:
        return False
    try:
        data = _serializer.loads(token, max_age=AUTH_MAX_AGE)
        return bool(data.get("ok"))
    except Exception:
        return False


def is_authenticated(request: Request) -> bool:
    return verify_auth_token(request.cookies.get(COOKIE_NAME))


def check_password(password: str) -> bool:
    return (password or "").strip() == settings.site_password


PUBLIC_PATHS = {
    "/login",
    "/api/login",
    "/health",
}


def is_public_path(path: str) -> bool:
    if path in PUBLIC_PATHS:
        return True
    if path.startswith("/static/"):
        return True
    return False


async def require_auth_middleware(request: Request, call_next):
    path = request.url.path
    if is_public_path(path) or is_authenticated(request):
        return await call_next(request)

    if path.startswith("/api/"):
        return JSONResponse({"detail": "Unauthorized"}, status_code=401)

    return RedirectResponse(url="/login", status_code=303)
