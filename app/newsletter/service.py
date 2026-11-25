"""Serwis newslettera: detekcja zmian (hash plików kierunku po refreshu),
maile potwierdzające, natychmiastowe powiadomienia o zmianach, wygaszanie
subskrypcji slugowych.
"""
from __future__ import annotations

import hashlib
import logging
import time
from datetime import datetime
from pathlib import Path

from ..scraping.catalog import load_catalog
from ..scraping.storage import PLAN_FILE, SALES_DIR, WEEK_FILE, scraped_root
from .smtp import SmtpConfig, send_mail
from .store import NewsletterStore

logger = logging.getLogger(__name__)

_EVENT_SUMMARY = "Dane kierunku zostały zaktualizowane (plan studiów / rozkład / terminarze)."
_EVENTS_TTL = 30 * 86400.0  # zdarzenia starsze niż miesiąc wypadają


class NewsletterService:
    def __init__(
        self,
        store: NewsletterStore,
        saves_store,  # SavesStore — payload slugów (scope=slug)
        cfg: SmtpConfig,
        data_dir: Path | str,
        *,
        secret: str = "",
    ) -> None:
        self.store = store
        self.saves = saves_store
        self.cfg = cfg
        self.data_dir = Path(data_dir)
        self._secret_key = secret

    # ---------- detekcja zmian (hook po refreshu) ----------

    async def on_course_refreshed(self, kid: int) -> None:
        """Hash treści katalogu kierunku (plan/week/sales — bez plików stanu
        z znacznikami czasu, które zmieniają się przy każdym refreshu).
        Zmiana → natychmiastowy mail do subskrybentów tego kierunku."""
        content_hash = self._course_content_hash(kid)
        if content_hash is None:
            return
        if not self.store.record_version(kid, content_hash):
            return
        self.store.add_event(kid, _EVENT_SUMMARY)
        logger.info("newsletter: wykryto zmianę danych kierunku kid=%d", kid)
        try:
            await self.notify_course(kid)
        except Exception:  # wysyłka nie może wywrócić workera odświeżania
            logger.exception("newsletter: powiadomienie o zmianie (kid=%d)", kid)
        finally:
            self.store.prune_events(time.time() - _EVENTS_TTL)

    def _course_content_hash(self, kid: int) -> str | None:
        root = scraped_root(self.data_dir) / str(kid)
        if not root.is_dir():
            return None
        digest = hashlib.sha256()
        found = False
        for path in sorted(root.rglob("*")):
            if not path.is_file():
                continue
            # tylko treść: plan.html, week.html, sales/{zid}.html — pliki
            # stanu (course.json) i meta.json niosą znaczniki czasu
            if path.name not in (PLAN_FILE, WEEK_FILE) and path.parent.name != SALES_DIR:
                continue
            try:
                digest.update(path.relative_to(root).as_posix().encode("utf-8"))
                digest.update(b"\0")
                digest.update(path.read_bytes())
                digest.update(b"\0")
                found = True
            except OSError:
                logger.warning("newsletter: nie dało się przeczytać %s", path)
                return None
        return digest.hexdigest() if found else None

    def _course_name(self, kid: int) -> str:
        try:
            catalog = load_catalog(self.data_dir)
        except Exception:  # uszkodzony katalog nie może wywrócić wysyłki
            return f"kierunek {kid}"
        for faculty in catalog.values():
            entry = (faculty.get("courses") or {}).get(str(kid))
            if entry and entry.get("name"):
                return str(entry["name"])
        return f"kierunek {kid}"

    # ---------- maile ----------

    async def _send_mail(self, to: str, subject: str, body: str) -> None:
        await send_mail(self.cfg, to, subject, body)

    def _footer(self, sub: dict) -> str:
        from .tokens import sign_token

        secret = self._secret
        out = sign_token({"sid": sub["id"], "email": sub["email"], "act": "out"}, secret)
        manage = sign_token({"email": sub["email"], "act": "manage"}, secret)
        base = self.cfg.base_url
        return (
            "\n--\n"
            "Otrzymujesz tę wiadomość, bo obserwujesz zmiany w kreatorze planu\n"
            "zajęć. Adres e-mail służy wyłącznie do wysyłki tych powiadomień,\n"
            "nie jest udostępniany nikomu innemu.\n"
            f"Zrezygnuj: {base}/newsletter/out?token={out}\n"
            f"Zarządzaj subskrypcjami: {base}/newsletter/manage?token={manage}\n"
        )

    async def send_confirmation(self, sub: dict) -> None:
        from .tokens import sign_token

        token = sign_token(
            {"sid": sub["id"], "email": sub["email"], "act": "confirm"}, self._secret
        )
        url = f"{self.cfg.base_url}/newsletter/confirm?token={token}"
        body = (
            "Cześć,\n\n"
            "ktoś zapisał ten adres do obserwowania zmian w kreatorze planu\n"
            "zajęć. Potwierdź subskrypcję klikając w link:\n\n"
            f"{url}\n\n"
            "Bez potwierdzenia nie wysyłamy żadnych powiadomień.\n"
            "Nie zapisywał(aś/eś się? Zignoruj tę wiadomość — nic nie robimy.\n"
        )
        await self._send_mail(sub["email"], "Potwierdź obserwowanie zmian — plan zajęć", body)

    async def notify_course(self, kid: int) -> int:
        """Wysyła powiadomienie o zmianach w kierunku do każdego potwierdzonego
        subskrybenta (course scope na ten kid + slugi wskazujące ten kid, jeśli
        mają wybrane przedmioty). Zwraca liczbę wysłanych maili; zdarzenia są
        oznaczane jako wysłane tylko dla udanych adresów."""
        sent = 0
        for email, subs in self.store.confirmed_by_email().items():
            blocks = []
            for sub in subs:
                if sub["scope"] == "course":
                    if sub["kid"] != kid:
                        continue
                else:
                    if self._slug_kid(sub["slug"]) != kid:
                        continue
                    if self._slug_selected_count(sub["slug"]) == 0:
                        continue
                sub_events = self.store.events_since(kid, sub["last_sent"])
                if sub_events:
                    blocks.append((sub, kid, sub_events))
            if not blocks:
                continue
            try:
                await self._send_change_mail(email, blocks)
                for sub, _kid, _events in blocks:
                    self.store.mark_sent(sub["id"], time.time())
                sent += 1
            except Exception:
                logger.exception("newsletter: powiadomienie do %s nie wyszło", email)
        return sent

    def _slug_kid(self, slug: str | None) -> int | None:
        if not slug:
            return None
        record = self.saves.get(slug)
        if record is None:
            return None
        kid = record.payload.get("kid")
        return kid if isinstance(kid, int) else None

    def _slug_selected_count(self, slug: str | None) -> int:
        if not slug:
            return 0
        record = self.saves.get(slug)
        if record is None:
            return 0
        selected = record.payload.get("selected")
        return len(selected) if isinstance(selected, list) else 0

    async def _send_change_mail(
        self, email: str, blocks: list[tuple[dict, int, list[dict]]]
    ) -> None:
        lines = ["Cześć,", "", "w obserwowanych kierunkach pojawiły się zmiany:", ""]
        for sub, kid, events in blocks:
            name = self._course_name(kid)
            lines.append(f"● {name}:")
            for ev in events:
                when = datetime.fromtimestamp(ev["created_at"]).strftime("%d.%m.%Y %H:%M")
                lines.append(f"  - {when} — {ev['summary']}")
            if sub["scope"] == "slug":
                n = self._slug_selected_count(sub["slug"])
                lines.append(
                    f"  (obserwujesz zapis /s/{sub['slug']} — zmiany dotyczą"
                    f" przedmiotów aktualnie na nim wybranych: {n})"
                )
            else:
                lines.append(f"  Zobacz plan: {self.cfg.base_url}/?kid={kid}")
            lines.append("")
        subject = (
            "Zmiany na obserwowanych kierunkach — plan zajęć"
            if len(blocks) > 1
            else f"Zmiany na kierunku {self._course_name(blocks[0][1])} — plan zajęć"
        )
        await self._send_mail(email, subject, "\n".join(lines) + self._change_footer(blocks))

    def _change_footer(self, blocks: list[tuple[dict, int, list[dict]]]) -> str:
        """Stopka powiadomienia: opt-out per subskrypcja (mail zbiera zmiany z
        wielu obserwacji — jeden link wypisuje tylko jedną z nich, więc
        przy kilku pokazujemy listę), zarządzanie i info o przetwarzaniu."""
        from .tokens import sign_token

        base = self.cfg.base_url
        manage = sign_token(
            {"email": blocks[0][0]["email"], "act": "manage"}, self._secret
        )
        lines = [
            "\n--\n",
            "Otrzymujesz tę wiadomość, bo obserwujesz zmiany w kreatorze planu\n"
            "zajęć. Adres e-mail służy wyłącznie do wysyłki tych powiadomień,\n"
            "nie jest udostępniany nikomu innemu.\n",
        ]
        if len(blocks) == 1:
            sub = blocks[0][0]
            out = sign_token(
                {"sid": sub["id"], "email": sub["email"], "act": "out"}, self._secret
            )
            lines.append(f"Zrezygnuj: {base}/newsletter/out?token={out}\n")
        else:
            lines.append("Rezygnacja (każda obserwacja osobno):")
            for sub, _kid, _events in blocks:
                what = (
                    f"kierunek {sub['kid']}" if sub["scope"] == "course"
                    else f"zapis /s/{sub['slug']}"
                )
                out = sign_token(
                    {"sid": sub["id"], "email": sub["email"], "act": "out"},
                    self._secret,
                )
                lines.append(f"  - {what}: {base}/newsletter/out?token={out}")
        lines.append(f"Zarządzaj subskrypcjami: {base}/newsletter/manage?token={manage}\n")
        return "\n".join(lines)

    async def notify_slug_expired(self, slugs: list[str]) -> None:
        """Slug usunięty przez retencję → ostatni mail o wygaśnięciu
        subskrypcji, potem usunięcie subskrypcji (decyzja z planu)."""
        for slug in slugs:
            subs = self.store.slug_subscriptions(slug)
            if not subs:
                continue
            for sub in subs:
                try:
                    await self._send_mail(
                        sub["email"],
                        "Subskrypcja wygasła — plan zajęć",
                        "Cześć,\n\n"
                        f"zapis /s/{slug} został usunięty — nie był otwierany"
                        " przez okres retencji, więc subskrypcja zmian właśnie\n"
                        "wygasła. Jeśli założysz nowy zapis, możesz obserwować\n"
                        "zmiany od nowa.\n",
                    )
                except Exception:
                    logger.exception("newsletter: mail o wygaśnięciu (%s)", sub["email"])
            self.store.delete_slug_subscriptions(slug)

    # ---------- konfiguracja ----------

    @property
    def _secret(self) -> str:
        return self._secret_key

    def close(self) -> None:
        self.store.close()
