"""Wysyłka maili przez SMTP (Stalwart) — aiosmtplib, tryby starttls/ssl/none.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SmtpConfig:
    host: str
    port: int
    user: str
    password: str
    from_addr: str
    tls: str  # starttls | ssl | none
    base_url: str  # absolutne linki w mailach (confirm/out/manage)

    @classmethod
    def from_env(cls, env: dict[str, str]) -> "SmtpConfig":
        def _int(raw: str | None, default: int) -> int:
            try:
                return int(raw) if raw not in (None, "") else default
            except ValueError:
                logger.warning("SMTP_PORT: zła wartość %r — używam %d", raw, default)
                return default

        return cls(
            host=(env.get("SMTP_HOST") or "").strip(),
            port=_int(env.get("SMTP_PORT"), 587),
            user=(env.get("SMTP_USER") or "").strip(),
            password=env.get("SMTP_PASSWORD") or "",
            from_addr=(env.get("SMTP_FROM") or "").strip() or "newsletter@localhost",
            tls=(env.get("SMTP_TLS") or "starttls").strip().lower(),
            base_url=(env.get("BASE_URL") or "http://localhost:8000").strip().rstrip("/"),
        )


async def send_mail(cfg: SmtpConfig, to: str, subject: str, body: str) -> None:
    """Wysyła pojedynczy mail tekstowy. Import aiosmtplib leniwy — moduł
    jest używany wyłącznie przy skonfigurowanym SMTP."""
    import aiosmtplib
    from email.message import EmailMessage

    msg = EmailMessage()
    msg["From"] = cfg.from_addr
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)

    kwargs: dict = {"hostname": cfg.host, "port": cfg.port}
    if cfg.user:
        kwargs.update(username=cfg.user, password=cfg.password)
    if cfg.tls == "ssl":
        kwargs["use_tls"] = True
    elif cfg.tls == "starttls":
        kwargs["start_tls"] = True
    await aiosmtplib.send(msg, **kwargs)
