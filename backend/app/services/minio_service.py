from app.core.storage import storage_client


def upload_to_storage(file_name: str, file_bytes: bytes, content_type: str = "application/octet-stream"):
    return storage_client.upload_bytes(file_name, file_bytes, content_type)
