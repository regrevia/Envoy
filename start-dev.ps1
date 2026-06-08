docker compose up -d mysql redis minio
Set-Location backend
python -m uvicorn app.main:app --reload --port 8000
