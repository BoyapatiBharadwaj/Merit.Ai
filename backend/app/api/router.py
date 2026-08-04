"""
Aggregates all v1 routers under a single /api/v1 prefix.
"""
from fastapi import APIRouter

from app.api.v1 import (access_requests, admin, analytics, attempts, auth, exams, organizations,
                        proctoring, users)

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(auth.router)
api_router.include_router(access_requests.router)
api_router.include_router(users.router)
api_router.include_router(organizations.router)
api_router.include_router(exams.router)
api_router.include_router(attempts.router)
api_router.include_router(proctoring.router)
api_router.include_router(analytics.router)
api_router.include_router(admin.router)
