from app.schemas.artifact import ArtifactCreate


def create_artifact_service(payload: ArtifactCreate):
    return payload.model_dump()
