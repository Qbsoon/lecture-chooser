"""Zapisy planów po stronie serwera (v4, Faza 2) — styl „codeshare".

Dowolny ciąg po ``/s/<slug>`` można zająć jako swój, opcjonalnie z hasłem
(argon2id). Slug bez hasła jest publiczny — każdy może go nadpisać.
Każde otwarcie odświeża ``last_opened``; zapisy nieużywane dłużej niż
``retention_days`` usuwa codzienne zadanie w tle (retencja).
"""
