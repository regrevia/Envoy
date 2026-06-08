def build_upload_record(file_name: str, file_path: str, mime_type: str | None = None):
    return {
        "file_name": file_name,
        "file_path": file_path,
        "mime_type": mime_type,
    }
