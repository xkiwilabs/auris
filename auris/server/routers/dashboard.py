from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse

router = APIRouter()

_INDEX = Path(__file__).resolve().parent.parent / "static" / "index.html"


@router.get("/", include_in_schema=False)
async def dashboard() -> FileResponse:
    """Serve the single-file browser dashboard (open — the page carries no secrets)."""
    return FileResponse(_INDEX, media_type="text/html")
