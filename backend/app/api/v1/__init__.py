from fastapi import APIRouter

from app.api.v1 import auth, brands, users

# Feature routers are included here as each milestone lands.
api_router = APIRouter(prefix="/api/v1")
api_router.include_router(auth.router)
api_router.include_router(users.router)
api_router.include_router(brands.router)
