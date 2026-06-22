"""M4 catalog API package."""
import asyncio
import sys

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    try:
        import uvicorn.loops.asyncio as _uloop

        def _selector_factory(use_subprocess: bool = False):
            return asyncio.SelectorEventLoop

        _uloop.asyncio_loop_factory = _selector_factory
    except ImportError:
        pass

