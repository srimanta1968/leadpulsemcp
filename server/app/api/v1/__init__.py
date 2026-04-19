from fastapi import APIRouter

from app.api.v1.endpoints import admin, bootstrap, contacts_callback

router = APIRouter()
router.include_router(bootstrap.router, prefix="/bootstrap", tags=["bootstrap"])
router.include_router(contacts_callback.router, prefix="/contacts", tags=["contacts-callback"])
router.include_router(admin.router, prefix="/admin", tags=["admin"])
