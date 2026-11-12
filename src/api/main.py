"""FastAPI application for CDSS Knowledge Engine."""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from src.review.errors import ReviewError

from .routes import pdf, kg, rules, engine, standards, inference, templates, vocab, guide_packages, neo4j_graph, profiles, auto_import, kg_review

app = FastAPI(title="CDSS Knowledge Engine", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(pdf.router)
app.include_router(kg.router)
app.include_router(rules.router)
app.include_router(engine.router)
app.include_router(standards.router)
app.include_router(inference.router)
app.include_router(templates.router)
app.include_router(vocab.router)
app.include_router(guide_packages.router)
app.include_router(neo4j_graph.router)
app.include_router(profiles.router)
app.include_router(auto_import.router)
from src.api.routes import import_jobs
app.include_router(import_jobs.router)
app.include_router(kg_review.router)
app.include_router(kg_review.audit_router)


@app.exception_handler(ReviewError)
async def review_error_handler(request, exc: ReviewError):
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})

STATIC_DIR = Path(__file__).parent / "static"


@app.get("/api/health")
async def health():
    return {"status": "ok"}


@app.get("/")
async def index():
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

