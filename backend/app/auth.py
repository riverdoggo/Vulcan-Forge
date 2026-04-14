from fastapi import Header, HTTPException, Query, status

from app.config.settings import VULCAN_API_KEY


async def require_api_key(
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    api_key: str | None = Query(default=None),
) -> None:
    """
    Dependency — inject into routes that need auth.
    If VULCAN_API_KEY is not configured, this is a no-op (dev mode).
    """
    if not VULCAN_API_KEY:
        return
    presented_key = (x_api_key or api_key or "")
    if presented_key != VULCAN_API_KEY:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API key",
            headers={"WWW-Authenticate": "ApiKey"},
        )
