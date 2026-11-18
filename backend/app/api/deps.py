from collections.abc import AsyncGenerator

from app.api.errors import api_error
from app.core.config import settings
from app.integrations.canvas.client import CanvasClient


async def get_canvas_client() -> AsyncGenerator[CanvasClient, None]:
    if not settings.canvas_configured:
        raise api_error(412, "canvas_not_configured")
    client = CanvasClient(settings.canvas_base_url, settings.canvas_access_token)
    try:
        yield client
    finally:
        await client.close()
