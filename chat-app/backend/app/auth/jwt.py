import json
import ssl
import time

import httpx
import jwt
from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt.algorithms import RSAAlgorithm

from app.registry.service import error
from app.settings import Settings, get_settings

bearer = HTTPBearer(auto_error=False)


class TokenValidator:
    def __init__(self):
        self._keys: dict[str, dict] = {}
        self._expires_at = 0.0

    async def validate(self, token: str, settings: Settings) -> str:
        return (await self.validate_claims(token, settings))["sub"]

    async def validate_claims(self, token: str, settings: Settings) -> dict:
        unauthorized = error(401, "unauthorized", "Valid authentication is required.")
        try:
            header = jwt.get_unverified_header(token)
            if header.get("alg") != "RS256" or not isinstance(header.get("kid"), str):
                raise unauthorized
            if time.monotonic() >= self._expires_at or header["kid"] not in self._keys:
                verify = True
                if settings.use_os_truststore:
                    import truststore

                    verify = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
                async with httpx.AsyncClient(verify=verify, follow_redirects=False, timeout=5) as client:
                    response = await client.get(settings.oidc_jwks_url)
                    response.raise_for_status()
                    payload = response.json()
                self._keys = {key["kid"]: key for key in payload["keys"] if key.get("kty") == "RSA" and key.get("alg", "RS256") == "RS256" and "kid" in key}
                self._expires_at = time.monotonic() + 300
            key = RSAAlgorithm.from_jwk(json.dumps(self._keys[header["kid"]]))
            claims = jwt.decode(token, key, algorithms=["RS256"], audience=settings.oidc_audience, issuer=settings.oidc_issuer, options={"require": ["sub", "iss", "aud", "exp"]})
            subject = claims["sub"]
            if not isinstance(subject, str) or not subject:
                raise unauthorized
            return claims
        except Exception as exc:
            raise unauthorized from exc


validator = TokenValidator()


async def current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    settings: Settings = Depends(get_settings),
) -> str:
    if not credentials or credentials.scheme.lower() != "bearer":
        raise error(401, "unauthorized", "Valid authentication is required.")
    return await validator.validate(credentials.credentials, settings)


async def current_admin(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    settings: Settings = Depends(get_settings),
) -> str:
    if not credentials or credentials.scheme.lower() != "bearer":
        raise error(401, "unauthorized", "Valid authentication is required.")
    claims = await validator.validate_claims(credentials.credentials, settings)
    groups = claims.get("groups", [])
    if not isinstance(groups, list) or "/admin" not in groups:
        raise error(403, "admin_required", "Membership in the admin group is required.")
    return claims["sub"]