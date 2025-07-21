from dsx_connect.utils.logging import dsx_logging
from dsx_connect.taskqueue.celery_app import celery_app
from dsx_connect.taskworkers.taskworkers import init_worker
from dsx_connect.config import ConfigManager

config = ConfigManager.reload_config()

"""
Primarily for use in debugging, this start script will start all Celery queues and the workers
"""

if __name__ == "__main__":
    # # Sample data for scan_request_task
    # sample_scan_request = {
    #     "location": "file.txt",
    #     "metainfo": "test scan",
    #     "connector_url": "http://example.com"
    # }
    #
    init_worker() # necessary for when running in debug mode
    # Configure and run the Celery worker
    dsx_logging.info("Starting Celery worker for debugging...")
    celery_app.worker_main([
        "worker",
        "--loglevel=info",
        "--pool=solo",  # <== allows for running in debugging mode.
        f"--queues={config.taskqueue.scan_request_queue},{config.taskqueue.verdict_action_queue},{config.taskqueue.scan_result_queue},{config.taskqueue.scan_result_notification_queue}",  #,{config.taskqueue.encrypted_file_queue}
        "--concurrency=1"
    ])
