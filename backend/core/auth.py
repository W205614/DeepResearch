"""OIDC verification and local-only development tokens."""
from dataclasses import dataclass
import time

import httpx
import jwt
from fastapi import HTTPException


@dataclass(frozen=True)
class Principal:
    subject: str
    display_name: str


class TokenVerifier:
    def __init__(self, settings):
        self.settings = settings
        self._jwks: dict | None = None
        self._jwks_at = 0.0

    async def _key_set(self) -> dict:
        if self._jwks and time.monotonic() - self._jwks_at < 600:
            return self._jwks
        if not self.settings.oidc_jwks_url:
            raise HTTPException(503, "OIDC JWKS 地址未配置")
        try:
            async with httpx.AsyncClient(timeout=5, trust_env=False) as client:
                response = await client.get(self.settings.oidc_jwks_url)
                response.raise_for_status()
                result = response.json()
            if not isinstance(result.get("keys"), list):
                raise ValueError("invalid JWKS")
        except Exception:
            raise HTTPException(503, "OIDC 身份服务暂时不可用") from None
        self._jwks, self._jwks_at = result, time.monotonic()
        return result

    async def verify(self, authorization: str) -> Principal:
        if not authorization.startswith("Bearer "):
            raise HTTPException(401, "请先登录")
        token = authorization.removeprefix("Bearer ").strip()
        try:
            if self.settings.auth_mode == "development":
                claims = jwt.decode(token, self.settings.development_jwt_secret.get_secret_value(),
                                    algorithms=["HS256"], audience=self.settings.oidc_audience,
                                    options={"require": ["exp", "sub", "aud"]})
            else:
                header = jwt.get_unverified_header(token)
                keys = (await self._key_set())["keys"]
                key = next((item for item in keys if item.get("kid") == header.get("kid")), None)
                if not key:
                    self._jwks = None
                    keys = (await self._key_set())["keys"]
                    key = next((item for item in keys if item.get("kid") == header.get("kid")), None)
                if not key:
                    raise jwt.InvalidTokenError("unknown key")
                claims = jwt.decode(token, jwt.PyJWK.from_dict(key).key, algorithms=["RS256"],
                                    audience=self.settings.oidc_audience, issuer=self.settings.oidc_issuer,
                                    options={"require": ["exp", "sub", "aud", "iss"]})
        except jwt.PyJWTError:
            raise HTTPException(401, "登录凭证无效或已过期") from None
        subject = str(claims.get("sub", ""))
        if not subject or len(subject) > 128:
            raise HTTPException(401, "登录凭证缺少有效主体")
        display = str(claims.get("preferred_username") or claims.get("name") or subject)[:100]
        return Principal(subject, display)


def issue_development_token(settings, subject: str, *, expires_in: int = 3600) -> str:
    """Test/demo helper; deliberately unavailable as an HTTP endpoint."""
    now = int(time.time())
    return jwt.encode({"sub": subject, "aud": settings.oidc_audience, "iat": now, "exp": now + expires_in},
                      settings.development_jwt_secret.get_secret_value(), algorithm="HS256")

