from celery import Celery

from app.core.config import get_settings

settings = get_settings()

celery_app = Celery("jobscout", broker=settings.redis_url, backend=settings.redis_url)
celery_app.conf.update(
    accept_content=["json"],
    beat_schedule={
        "foundation-heartbeat": {
            "task": "app.workers.tasks.heartbeat",
            "schedule": 60.0,
        },
        "enqueue-due-career-sources": {
            "task": "app.workers.tasks.enqueue_due_sources",
            "schedule": 60.0,
        },
        "dispatch-notification-outbox": {
            "task": "app.workers.tasks.dispatch_notifications",
            "schedule": 30.0,
        },
        "enqueue-due-review-reminders": {
            "task": "app.workers.tasks.enqueue_due_review_reminders",
            "schedule": 60.0,
        },
    },
    enable_utc=True,
    result_expires=3600,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    task_serializer="json",
    timezone="UTC",
    worker_prefetch_multiplier=1,
)
celery_app.autodiscover_tasks(["app.workers"])
