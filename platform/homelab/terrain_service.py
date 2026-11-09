"""Public terrain-only origin; no accounts, project storage or processing keys."""
from fastapi import FastAPI
from .terrain_gateway import router

app = FastAPI(title="MosaicEarth public terrain", docs_url=None, redoc_url=None, openapi_url=None)
app.include_router(router)


@app.get("/health")
def health():
    return {"status": "ok", "service": "public-terrain", "contract": 1}
