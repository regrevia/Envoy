from celery import Celery

from app.core.config import REDIS_URL

# The worker must import the job modules so their @celery_app.task functions get
# registered; a worker that only imports this module rejects every queued task with
# "Received unregistered task of type 'app.workers.jobs.*'".
celery_app = Celery(
    "envoy",
    broker=REDIS_URL,
    backend=REDIS_URL,
    include=["app.workers.jobs"],
)
celery_app.conf.task_routes = {"app.workers.jobs.*": {"queue": "envoy"}}
celery_app.conf.broker_connection_retry_on_startup = True
