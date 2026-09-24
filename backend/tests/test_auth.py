from datetime import datetime, timedelta, timezone

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException

from app.auth.jwt import TokenValidator
from app.settings import Settings


@pytest.mark.asyncio
async def test_jwks_validation_issuer_audience_and_exp():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key(), as_dict=True)
    settings = Settings(_env_file=None, oidc_issuer="https://issuer/realms/test", oidc_audience="swiss-discover-api")
    validator = TokenValidator()
    validator._keys = {"test-key": {**jwk, "kid": "test-key"}}
    validator._expires_at = float("inf")
    claims = {
        "iss": settings.oidc_issuer, "aud": settings.oidc_audience,
        "sub": "alice", "exp": datetime.now(timezone.utc) + timedelta(minutes=5),
    }

    def sign(payload):
        return jwt.encode(payload, key, algorithm="RS256", headers={"kid": "test-key"})

    assert await validator.validate(sign(claims), settings) == "alice"
    for override in ({"iss": "https://other"}, {"aud": "another-api"}, {"exp": datetime.now(timezone.utc) - timedelta(seconds=5)}, {"sub": ""}):
        with pytest.raises(HTTPException) as exception:
            await validator.validate(sign({**claims, **override}), settings)
        assert exception.value.status_code == 401
    with pytest.raises(HTTPException):
        await validator.validate(jwt.encode(claims, "secret", algorithm="HS256", headers={"kid": "test-key"}), settings)