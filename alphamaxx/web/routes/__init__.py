"""Route registration. Each module exposes register(app, rt)."""

from __future__ import annotations

from alphamaxx.web.routes import econ, home, lists, queue, search, stock, tools


def register_all(app, rt) -> None:
    home.register(app, rt)
    stock.register(app, rt)
    lists.register(app, rt)
    tools.register(app, rt)
    econ.register(app, rt)
    queue.register(app, rt)
    search.register(app, rt)
