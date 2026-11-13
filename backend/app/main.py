import logging
from app.version import VERSION
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.api.routes import router
from app.api.rf_routes import router as rf_router
from app.api.rf_analytics_routes import router as rf_analytics_router
from app.core.config import settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
app = FastAPI(title=settings.app_name, version=VERSION, docs_url="/docs" if settings.api_docs_enabled else None, redoc_url="/redoc" if settings.api_docs_enabled else None, openapi_url="/openapi.json" if settings.api_docs_enabled else None)
app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origin_list, allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

@app.get("/health")
def health():
    return {"status": "ok", "service": settings.app_name, "version": VERSION}

app.include_router(router)
app.include_router(rf_router)
app.include_router(rf_analytics_router)
