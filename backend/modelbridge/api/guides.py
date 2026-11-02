from html import escape
from importlib.resources import files
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse, PlainTextResponse

router = APIRouter(prefix="/api/guides", tags=["Developer guides"])
GUIDES = (
    "API_REFERENCE.md",
    "API_QUICKSTART.md",
    "API_AUTHENTICATION.md",
    "API_VERSIONING.md",
    "WEBHOOKS.md",
    "SDK_GUIDE.md",
    "SELF_HOSTING.md",
    "PRODUCTION_DEPLOYMENT.md",
    "API_SECURITY.md",
    "API_ACCEPTANCE_REPORT.md",
    "MIGRATION_EXECUTION.md",
    "BACKGROUND_JOBS.md",
    "RECONCILIATION.md",
    "DOCKER_DEPLOYMENT.md",
)


@router.get("", response_class=HTMLResponse)
def index() -> str:
    links = "".join(
        f'<li><a href="/api/guides/{escape(name)}">{escape(name)}</a></li>' for name in GUIDES
    )
    return (
        "<!doctype html><title>ModelBridge developer guides</title>"
        "<h1>ModelBridge API: development in progress</h1>"
        '<p><a href="/api/docs">Interactive API explorer</a></p>'
        f"<ul>{links}</ul>"
    )


@router.get("/{guide}", response_class=PlainTextResponse)
def read_guide(guide: str) -> str:
    if guide not in GUIDES:
        raise HTTPException(404, "Guide not found")
    packaged = files("modelbridge").joinpath("guides", guide)
    if packaged.is_file():
        return packaged.read_text(encoding="utf-8")
    # Editable installations use the clone's docs; wheels include these resources.
    source = Path(__file__).resolve().parents[3] / "docs" / guide
    if source.is_file():
        return source.read_text(encoding="utf-8")
    raise HTTPException(503, "Guide unavailable in this installation")
