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
    if request.url.path.startswith(('/api/customers','/api/notifications','/api/down-detector','/api/tower-health','/api/ipam','/api/equipment','/api/field','/api/maintenance-ops','/api/applications','/api/unified','/api/planning','/api/fiber-designer','/api/finance','/api/analytics','/api/customer-status')):
        return JSONResponse(status_code=422, content={'detail':'Invalid customer request; check the documented field types and bounds'})
    return await request_validation_exception_handler(request, exc)

from app.notifications.routes import router as notification_router
app.include_router(notification_router)

from app.down_detector.routes import router as down_detector_router
app.include_router(down_detector_router)

from app.tower_health.routes import router as tower_health_router
app.include_router(tower_health_router)

from app.ipam.routes import router as ipam_router
app.include_router(ipam_router)

from app.equipment.routes import router as equipment_router
app.include_router(equipment_router)

from app.field.routes import router as field_router
app.include_router(field_router)

from app.maintenance_ops.routes import router as maintenance_ops_router
app.include_router(maintenance_ops_router)

from app.signup.routes import router as signup_router
app.include_router(signup_router)

# Application/contact searches and receipt tokens must not enter access logs.
from app.signup.privacy import configure as redact_access_queries
redact_access_queries()

from app.unified.routes import router as unified_router
app.include_router(unified_router)

from app.planning.routes import router as planning_router
app.include_router(planning_router)

# Reject oversized/unframed terrain requests before Starlette spools multipart files.
@app.middleware('http')
async def bounded_terrain_upload(request, call_next):
    if request.method == 'POST' and request.url.path == '/api/planning/datasets':
        from fastapi.responses import JSONResponse
        from app.core.security import decode_access_claims
        auth = request.headers.get('authorization', '')
        if not auth.startswith('Bearer ') or decode_access_claims(auth[7:]) is None:
            return JSONResponse({'detail':'Authenticated terrain upload required'},status_code=401)
        length = request.headers.get('content-length')
        if request.headers.get('transfer-encoding') or length is None:
            return JSONResponse({'detail':'Bounded Content-Length required for terrain upload'},status_code=411)
        try:
            if not 0 < int(length) <= settings.planning_max_upload_bytes + 16384:
                raise ValueError()
        except ValueError:
            return JSONResponse({'detail':'Terrain request exceeds configured import limit; pre-tile locally'},status_code=413)
    return await call_next(request)

from app.fiber_sync.routes import router as fiber_sync_router, machine_router as fiber_ingest_router
app.include_router(fiber_sync_router)
app.include_router(fiber_ingest_router)

@app.middleware('http')
async def bounded_fiber_exports(request, call_next):
    if request.method == 'POST' and request.url.path.startswith(('/api/fiber-designer/v1/','/api/fiber-designer-ingest/v1/')):
        from fastapi.responses import JSONResponse
        length=request.headers.get('content-length')
        if request.headers.get('transfer-encoding') or length is None:
            return JSONResponse({'detail':'Bounded Content-Length required'},status_code=411)
        try:
            if not 0<int(length)<=2200000:raise ValueError()
        except ValueError:
            return JSONResponse({'detail':'Fiber export request budget exceeded; partition approved revisions'},status_code=413)
    return await call_next(request)

from app.finance.routes import router as finance_router
app.include_router(finance_router)

@app.middleware("http")
async def bounded_financial_requests(request, call_next):
    if request.method=="POST" and request.url.path.startswith("/api/finance/"):
        try:
            if request.headers.get("transfer-encoding") or not 0<int(request.headers.get("content-length", "0"))<=1048576:raise ValueError()
        except ValueError:return JSONResponse({"detail":"Bounded financial request of at most 1 MiB required"},status_code=413)
    response=await call_next(request)
    if request.url.path.startswith("/api/finance/"):response.headers["Cache-Control"]="no-store"
    return response

from app.analytics.routes import router as analytics_router, scrape_router as analytics_scrape_router
app.include_router(analytics_router)
app.include_router(analytics_scrape_router)

@app.middleware("http")
async def bounded_analytics_requests(request,call_next):
    if request.method=="POST" and request.url.path.startswith(("/api/analytics/","/api/customer-status/")):
        try:
            if request.headers.get("transfer-encoding") or not 0<int(request.headers.get("content-length","0"))<=65536:raise ValueError()
        except ValueError:return JSONResponse({"detail":"Bounded analytics request required"},status_code=413)
    response=await call_next(request)
    if request.url.path.startswith(("/api/analytics/","/api/analytics-scrape/","/api/customer-status/")):response.headers["Cache-Control"]="no-store"
    return response

from app.customer_status.routes import router as customer_status_router
app.include_router(customer_status_router)

@app.middleware("http")
async def bounded_login_request(request, call_next):
    if request.method == "POST" and request.url.path == "/api/auth/login":
        body = bytearray()
        async for chunk in request.stream():
            if len(body) + len(chunk) > 8192:
                return JSONResponse({"detail": "Login request exceeds size limit"}, status_code=413,
                    headers={"Cache-Control": "no-store"})
            body.extend(chunk)
        request._body = bytes(body)
    response = await call_next(request)
    # Every /api route is private; public gateways are separate ASGI apps.
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    return response

from app.notifications.sms_routes import router as sms_router
app.include_router(sms_router)
