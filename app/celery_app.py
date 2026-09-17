"""Celery application for background tasks."""
import os
from celery import Celery

def make_celery(app_name=__name__):
    broker = os.getenv('CELERY_BROKER_URL', 'redis://localhost:6379/1')
    backend = os.getenv('CELERY_RESULT_BACKEND', 'redis://localhost:6379/2')
    celery = Celery(app_name, broker=broker, backend=backend)
    celery.conf.update(
        task_serializer='json',
        accept_content=['json'],
        result_serializer='json',
        timezone='UTC',
        enable_utc=True,
        task_acks_late=True,
        worker_prefetch_multiplier=1,
        task_reject_on_worker_lost=True,
    )
    return celery

celery = make_celery()
