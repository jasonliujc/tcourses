from fastapi import APIRouter

from app.api.v1 import courses, events, files, planner, resources, settings, sync, tasks

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(courses.router)
api_router.include_router(files.router)
api_router.include_router(resources.router)
api_router.include_router(planner.router)
api_router.include_router(tasks.router)
api_router.include_router(events.router)
api_router.include_router(sync.router)
api_router.include_router(settings.router)
