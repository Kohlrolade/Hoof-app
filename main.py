"""CLI entrypoint for local development."""

from huf_app.factory import create_app

app = create_app()
