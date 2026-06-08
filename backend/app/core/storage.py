from io import BytesIO
from pathlib import Path
from app.core.config import MINIO_ENDPOINT, MINIO_ACCESS_KEY, MINIO_SECRET_KEY, MINIO_BUCKET, STORAGE_MODE, LOCAL_STORAGE_DIR

try:
    from minio import Minio
except Exception:
    Minio = None


class StorageClient:
    def __init__(self, endpoint: str = MINIO_ENDPOINT, access_key: str = MINIO_ACCESS_KEY, secret_key: str = MINIO_SECRET_KEY, bucket: str = MINIO_BUCKET, mode: str = STORAGE_MODE, local_dir: Path = LOCAL_STORAGE_DIR):
        self.bucket = bucket
        self.endpoint = endpoint
        self.access_key = access_key
        self.secret_key = secret_key
        self.mode = mode
        self.local_dir = Path(local_dir)
        self.local_dir.mkdir(parents=True, exist_ok=True)
        self.client = None if mode == "local" or Minio is None else Minio(endpoint, access_key=access_key, secret_key=secret_key, secure=False)

    def _local_path(self, object_name: str):
        safe_name = Path(object_name).name
        if not safe_name:
            safe_name = "upload.bin"
        return self.local_dir / safe_name

    def upload_bytes(self, object_name: str, data: bytes, content_type: str = "application/octet-stream"):
        if self.mode == "local":
            path = self._local_path(object_name)
            path.write_bytes(data)
            return {"bucket": self.bucket, "object_name": path.name, "storage": "local", "file_path": str(path), "content_type": content_type}
        if self.client is None:
            return {"bucket": self.bucket, "object_name": object_name, "storage": "local-placeholder", "reason": "minio-sdk-missing"}
        try:
            if not self.client.bucket_exists(self.bucket):
                self.client.make_bucket(self.bucket)
            self.client.put_object(self.bucket, object_name, BytesIO(data), len(data), content_type=content_type)
            return {"bucket": self.bucket, "object_name": object_name, "storage": "minio"}
        except Exception as exc:
            return {"bucket": self.bucket, "object_name": object_name, "storage": "local-placeholder", "reason": str(exc)}

    def read_bytes(self, object_name: str):
        if self.mode == "local":
            return self._local_path(object_name).read_bytes()
        raise RuntimeError("read_bytes is only available for local storage mode")

    def read_text(self, object_name: str, encoding: str = "utf-8"):
        return self.read_bytes(object_name).decode(encoding)


storage_client = StorageClient()
