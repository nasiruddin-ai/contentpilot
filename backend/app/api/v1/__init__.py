from fastapi import APIRouter

from app.api.v1 import (
    auth,
    brands,
    calendar,
    notifications,
    opportunities,
    posts,
    publishing,
    research,
    social,
    sources,
    users,
    visuals,
)

# Feature routers are included here as each milestone lands.
api_router = APIRouter(prefix="/api/v1")
api_router.include_router(auth.router)
api_router.include_router(users.router)
api_router.include_router(brands.router)
api_router.include_router(sources.router)
api_router.include_router(research.router)
api_router.include_router(opportunities.router)
api_router.include_router(posts.router)
api_router.include_router(visuals.router)
api_router.include_router(calendar.router)
api_router.include_router(social.router)
api_router.include_router(publishing.router)
api_router.include_router(notifications.router)
