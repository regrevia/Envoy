from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.health import router as health_router
from app.api.v1.projects import router as projects_router
from app.api.v1.assets import router as assets_router
from app.api.v1.tasks import router as tasks_router
from app.api.v1.artifacts import router as artifacts_router
from app.api.v1.semantic_units import router as semantic_units_router
from app.api.v1.generated_samples import router as generated_samples_router
from app.api.v1.trace import router as trace_router
from app.api.v1.train_exports import router as train_exports_router

app = FastAPI(title="Envoy API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://127.0.0.1:8200",
        "http://localhost:8200",
        "http://127.0.0.1:3000",
        "http://localhost:3000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health_router, prefix="/api/v1", tags=["health"])
app.include_router(projects_router, prefix="/api/v1", tags=["projects"])
app.include_router(assets_router, prefix="/api/v1", tags=["assets"])
app.include_router(tasks_router, prefix="/api/v1", tags=["tasks"])
app.include_router(artifacts_router, prefix="/api/v1", tags=["artifacts"])
app.include_router(semantic_units_router, prefix="/api/v1", tags=["semantic-units"])
app.include_router(generated_samples_router, prefix="/api/v1", tags=["generated-samples"])
app.include_router(trace_router, prefix="/api/v1", tags=["trace"])
app.include_router(train_exports_router, prefix="/api/v1", tags=["train-exports"])
