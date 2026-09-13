"""Punkt wejścia ASGI dla hypercorn.

``app/__init__.py`` buduje instancję aplikacji leniwie przez PEP 562
``__getattr__``. Hypercorn ładuje cel przez ``eval`` na ``vars(module)``,
co omija ``__getattr__`` — dlatego ten moduł tworzy instancję eagerly.
"""
from app import create_app

app = create_app()
