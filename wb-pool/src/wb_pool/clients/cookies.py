"""browser_cookie3 wrapper — импорт cabinet cookies из реального Chrome.

Требования (см. docs/01-onboarding.md):
- Пользователь залогинен в Chrome → seller-content.wildberries.ru
- Chrome закрыт (иначе SQLite Cookies файл locked)
- macOS: терминалу нужен Full Disk Access
"""

from __future__ import annotations

import pickle
from datetime import UTC, datetime
from http.cookiejar import Cookie
from pathlib import Path


def import_chrome_cookies(
    *, output_path: Path, cookie_file: Path | None = None
) -> int:
    """Импортирует *.wildberries.ru cookies из Chrome → pickle. Returns count."""
    import browser_cookie3

    cj = browser_cookie3.chrome(
        cookie_file=str(cookie_file) if cookie_file else None,
        domain_name="wildberries.ru",
    )
    relevant = [c for c in cj if "wildberries.ru" in (c.domain or "")]
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("wb") as f:
        pickle.dump(relevant, f)
    output_path.chmod(0o600)
    return len(relevant)


def load_cookies(path: Path | None = None) -> list[Cookie]:
    p = path
    if p is None:
        from wb_pool.config import get_settings

        p = Path(get_settings().wb_cabinet_cookies_path)
    if not p.exists():
        raise FileNotFoundError(
            f"Cookies not imported ({p}). Run: wb-pool setup-cookies"
        )
    with p.open("rb") as f:
        cookies: list[Cookie] = pickle.load(f)
    return cookies


def oldest_expiry(cookies: list[Cookie]) -> datetime | None:
    """Самый ранний expires среди session cookies (None если все session-only)."""
    expiries = [c.expires for c in cookies if c.expires]
    if not expiries:
        return None
    return datetime.fromtimestamp(min(expiries), tz=UTC)


__all__ = ["import_chrome_cookies", "load_cookies", "oldest_expiry"]
