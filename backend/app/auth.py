from fastapi import Header, HTTPException, status

from app.config.settings import VULCAN_API_KEY


async def require_api_key(x_api_key: str | None = Header(default=None, alias="X-API-Key")) -> None:
    """
    Dependency — inject into routes that need auth.
    If VULCAN_API_KEY is not configured, this is a no-op (dev mode).
    """
    if not VULCAN_API_KEY:
        return
    if (x_api_key or "") != VULCAN_API_KEY:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing X-API-Key header",
            headers={"WWW-Authenticate": "ApiKey"},
        )
