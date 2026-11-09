"""CDSS API package.

Keep package import lightweight: importing a route module must not eagerly
build the whole FastAPI app (that creates circular imports between route
services and review modules).  Use ``src.api.main:app`` as the entry point.
"""
