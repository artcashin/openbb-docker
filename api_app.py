"""FastAPI app factory for `openbb-api` — the Platform REST app with
Private Network Access CORS enabled.

Why this file exists
--------------------
OpenBB Workspace runs at https://pro.openbb.co — a public origin. When the
browser classifies this backend's address as private/local (see Ep. 2's
gotchas), the fetch is gated behind an OPTIONS preflight carrying
`Access-Control-Request-Private-Network: true`. Starlette's CORSMiddleware
answers that header only when it was constructed with
`allow_private_network=True`, and OpenBB exposes no setting for it —
`api_settings.cors` carries allow_origins / allow_methods / allow_headers and
nothing else.

Earlier releases got there by rewriting `openbb_core/api/rest_api.py` inside
site-packages at image build time. That worked, but it was a text
substitution against upstream source: it goes silent the day upstream
reformats that block, and it hardcoded the interpreter's site-packages path,
so a base-image Python bump would have broken it too. `openbb-api` documents
a supported entrypoint for serving a customized instance — `--app <file>
--name <fn> --factory` — so the customization lives here, in version
controlled code, instead.

The subtlety that shapes the code below
---------------------------------------
`openbb-api` adds its OWN CORSMiddleware to whatever the factory returns
(`openbb_platform_api.utils.api.import_app`), and Starlette's
`add_middleware` does `user_middleware.insert(0, ...)` — index 0 is
OUTERMOST. A CORSMiddleware answers a preflight and returns without calling
anything downstream, so that outer layer, not ours, is the one the browser
actually talks to. Fixing only the instance `rest_api.py` registered would
therefore be invisible at runtime.

So this factory does two things:

1. Enables the flag on the CORS layer `rest_api.py` already registered.
2. Shadows `add_middleware` **on this one app object** (an instance
   attribute, not a patch of the class) so any CORS layer added after the
   factory returns inherits the flag as well.

Every failure mode here raises. A backend that answers preflights without the
private-network header is one Workspace silently cannot reach, which is worse
to debug than a container that refuses to start.
"""


def main():
    """Build the OpenBB Platform REST app with private-network CORS."""
    # pylint: disable=import-outside-toplevel
    from inspect import signature

    from fastapi.middleware.cors import CORSMiddleware
    from openbb_core.api.rest_api import app

    # Fail here, with this message, rather than as a TypeError raised from
    # inside uvicorn's startup when the middleware stack is finally built.
    if "allow_private_network" not in signature(CORSMiddleware.__init__).parameters:
        raise RuntimeError(
            "This Starlette's CORSMiddleware has no `allow_private_network` "
            "parameter. Chrome's Private Network Access preflight cannot be "
            "answered, so OpenBB Workspace would not reach this backend."
        )

    cors_layers = [mw for mw in app.user_middleware if mw.cls is CORSMiddleware]
    if not cors_layers:
        raise RuntimeError(
            "openbb_core.api.rest_api registered no CORSMiddleware — upstream "
            "changed. Refusing to start rather than serve a backend that "
            "Workspace cannot reach."
        )
    for layer in cors_layers:
        layer.kwargs["allow_private_network"] = True

    # `openbb-api` re-adds CORS outermost after this returns; make sure that
    # one carries the flag too. Bound to the instance so nothing else in the
    # process is affected — `setdefault`, so an explicit caller still wins.
    add_middleware = type(app).add_middleware

    def _add_middleware(middleware_class, *args, **kwargs):
        if middleware_class is CORSMiddleware:
            kwargs.setdefault("allow_private_network", True)
        return add_middleware(app, middleware_class, *args, **kwargs)

    app.add_middleware = _add_middleware

    return app
