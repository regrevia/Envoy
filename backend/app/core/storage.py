from io import BytesIO
from app.core.config import MINIO_ENDPOINT, MINIO_ACCESS_KEY, MINIO_SECRET_KEY, MINIO_BUCKET, STORAGE_MODE

try:
    from minio import Minio
except Exception:
    Minio = None


class StorageClient:
    def __init__(self, endpoint: str = MINIO_ENDPOINT, access_key: str = MINIO_ACCESS_KEY, secret_key: str = MINIO_SECRET_KEY, bucket: str = MINIO_BUCKET, mode: str = STORAGE_MODE):
        self.bucket = bucket
        self.endpoint = endpoint
        self.access_key = access_key
        self.secret_key = secret_key
        self.mode = mode
        self.client = None if mode == "local" or Minio is None else Minio(endpoint, access_key=access_key, secret_key=secret_key, secure=False)

    def upload_bytes(self, object_name: str, data: bytes, content_type: str = "application/octet-stream"):
        if self.mode == "local":
            return {"bucket": self.bucket, "object_name": object_name, "storage": "local-placeholder", "reason": "local-storage-mode"}
        if self.client is None:
            return {"bucket": self.bucket, "object_name": object_name, "storage": "local-placeholder", "reason": "minio-sdk-missing"}
        try:
            if not self.client.bucket_exists(self.bucket):
                self.client.make_bucket(self.bucket)
            self.client.put_object(self.bucket, object_name, BytesIO(data), len(data), content_type=content_type)
            return {"bucket": self.bucket, "object_name": object_name, "storage": "minio"}
        except Exception as exc:
            return {"bucket": self.bucket, "object_name": object_name, "storage": "local-placeholder", "reason": str(exc)}


storage_client = StorageClient()
