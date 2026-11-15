"""Client for the Remocon NET web app routes (R2)."""

from __future__ import annotations

import logging
import re
import time
from typing import Any

import aiohttp

_LOGGER = logging.getLogger(__name__)

LOGIN_PAGE = "/R2/Account/Login"
BSB_DATA_POINTS = "/R2/PlantMenuBsb/ReadDataPoints/{gateway}"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0 Safari/537.36"
)
TOKEN_PATTERN = re.compile(
    r'name="__RequestVerificationToken"[^>]*value="([^"]+)"'
)


class R2Error(Exception):
    """Raised when the web app answers with an error."""


class R2Client:
    """Read device data points through the Remocon NET web app."""

    def __init__(self, base_url: str, username: str, password: str) -> None:
        """Initialize the client for one account."""
        self._base_url = base_url.rstrip("/")
        self._username = username
        self._password = password
        self._session: aiohttp.ClientSession | None = None

    async def async_close(self) -> None:
        """Close the HTTP session."""
        if self._session is not None and not self._session.closed:
            await self._session.close()
        self._session = None

    async def _async_session(self) -> aiohttp.ClientSession:
        """Return the session holding the web app cookies."""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                cookie_jar=aiohttp.CookieJar(),
                headers={"User-Agent": USER_AGENT},
            )
        return self._session

    async def async_login(self) -> None:
        """Log in to the web app with the anti-forgery token form."""
        session = await self._async_session()
        async with session.get(f"{self._base_url}{LOGIN_PAGE}") as response:
            if response.status != 200:
                raise R2Error(f"Login page returned HTTP {response.status}")
            page = await response.text()

        match = TOKEN_PATTERN.search(page)
        if match is None:
            raise R2Error("Login page has no verification token")

        async with session.post(
            f"{self._base_url}{LOGIN_PAGE}",
            data={
                "__RequestVerificationToken": match.group(1),
                "email": self._username,
                "password": self._password,
                "rememberMe": "false",
            },
            allow_redirects=False,
        ) as response:
            if response.status not in (200, 302):
                raise R2Error(f"Login returned HTTP {response.status}")

    async def async_read_data_points(
        self, gateway: str, addresses: list[int]
    ) -> dict[int, dict[str, Any]]:
        """Read data points by address, logging in again on an expired session."""
        session = await self._async_session()
        path = f"{self._base_url}{BSB_DATA_POINTS.format(gateway=gateway)}"
        params = {
            "addresses": ",".join(str(address) for address in addresses),
            "rnd": str(int(time.time() * 1000)),
        }

        for _ in range(2):
            async with session.get(
                path, params=params, allow_redirects=False
            ) as response:
                if response.status in (301, 302, 303):
                    await self.async_login()
                    continue
                if response.status != 200:
                    raise R2Error(f"Data points returned HTTP {response.status}")
                payload = await response.json(content_type=None)

            if not payload.get("ok"):
                raise R2Error(f"Data points rejected: {payload.get('message')}")

            return {
                int(item["address"]): item for item in payload.get("data") or []
            }

        raise R2Error("Data points kept redirecting to the login page")
