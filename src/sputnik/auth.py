from __future__ import annotations

import hmac

from fastapi import HTTPException, Request, status


def secrets_match(provided: str | None, expected: str | None) -> bool:
    if not provided or not expected:
        return False
    return hmac.compare_digest(provided.encode("utf-8"), expected.encode("utf-8"))


def _bearer_token(request: Request) -> str | None:
    value = request.headers.get("Authorization", "")
    scheme, _, token = value.partition(" ")
    if scheme.lower() != "bearer" or not token:
        return None
    return token.strip()


def require_reader(request: Request) -> None:
    token = _bearer_token(request)
    settings = request.app.state.settings
    if not any(
        secrets_match(token, expected)
        for expected in (settings.api_key, settings.operator_key, settings.admin_key)
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="valid read-only bearer token required",
            headers={"WWW-Authenticate": "Bearer"},
        )


def require_operator(request: Request) -> None:
    token = _bearer_token(request)
    settings = request.app.state.settings
    if not any(
        secrets_match(token, expected)
        for expected in (settings.operator_key, settings.admin_key)
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="valid bounded-operator bearer token required",
            headers={"WWW-Authenticate": "Bearer"},
        )


def require_admin(request: Request) -> None:
    if not secrets_match(_bearer_token(request), request.app.state.settings.admin_key):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="valid admin bearer token required",
            headers={"WWW-Authenticate": "Bearer"},
        )
