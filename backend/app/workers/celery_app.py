from celery import Celery
from app.core.config import REDIS_URL

celery_app = Celery("envoy", broker=REDIS_URL, backend=REDIS_URL)
celery_app.conf.task_routes = {"app.workers.jobs.*": {"queue": "envoy"}}
