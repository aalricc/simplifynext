"""Back-compat shim. The real setup moved to shared/asgi.py.

Every service already called `add_cors(app)`, so pointing that name at
`configure()` gives all five of them the tenant middleware without touching
each service module. New code should import from shared.asgi directly.
"""

from shared.asgi import add_cors as _add_cors  # noqa: F401
from shared.asgi import add_tenant_middleware, configure


def add_cors(app) -> None:
    configure(app)


__all__ = ["add_cors", "add_tenant_middleware", "configure"]
