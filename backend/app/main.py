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

from app.api.rf_troubleshooting_routes import router as troubleshooting_router
app.include_router(troubleshooting_router)
from app.api.rf_operations_routes import router as rf_operations_router
app.include_router(rf_operations_router)
from app.api.monitoring_reliability_routes import router as monitoring_reliability_router
app.include_router(monitoring_reliability_router)

from app.api.session_routes import router as session_router
app.include_router(session_router)

from app.customers.routes import router as customer_router
app.include_router(customer_router)

from fastapi.exceptions import RequestValidationError
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.responses import JSONResponse

@app.exception_handler(RequestValidationError)
async def private_customer_validation(request, exc):
    if request.url.path.startswith('/api/customers'):
        return JSONResponse(status_code=422, content={'detail':'Invalid customer request; check the documented field types and bounds'})
    return await request_validation_exception_handler(request, exc)
