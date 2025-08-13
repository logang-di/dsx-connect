from celery import Celery
from dsx_connect.config import config

# Initialize Celery app
celery_app = Celery(
    config.taskqueue.name,
    broker=config.taskqueue.broker,
    backend=config.taskqueue.backend,
    include=["dsx_connect.taskworkers.taskworkers"]
)

# Configure queues and routing
celery_app.conf.task_queues = {
    config.taskqueue.scan_request_queue: {"exchange": config.taskqueue.scan_request_queue,
                                          "routing_key": "scan_request"},
    config.taskqueue.scan_result_queue: {"exchange": config.taskqueue.scan_result_queue, "routing_key": "scan_result"}
}
celery_app.conf.task_default_queue = config.taskqueue.scan_request_queue
celery_app.conf.task_routes = {
    config.taskqueue.scan_request_task: {"queue": config.taskqueue.scan_request_queue},
    config.taskqueue.verdict_action_task: {"queue": config.taskqueue.scan_result_queue}
}
celery_app.conf.task_serializer = "json"
celery_app.conf.result_serializer = "json"
celery_app.conf.accept_content = ["json"]
celery_app.conf.task_annotations = {'*': {'rate_limit': '10/s'}}
celery_app.conf.task_always_eager = False
celery_app.conf.worker_concurrency = 1  # still limits concurrency per worker


def get_celery():
    return celery_app
