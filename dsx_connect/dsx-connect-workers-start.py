from dsx_connect.config import get_config
from shared.dsx_logging import dsx_logging
from dsx_connect.taskworkers.celery_app import celery_app
from dsx_connect.taskworkers.taskworkers_deprecated import init_worker
from dsx_connect.taskworkers.names import Queues

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
    # Get the config to ensure we're using the right broker/backend
    config = get_config()

    # Print debug info
    print(f"Worker will connect to broker: {config.workers.broker}")
    print(f"Worker will connect to backend: {config.workers.backend}")
    print(f"Queues to listen on:")
    print(f"  - {Queues.REQUEST}")
    print(f"  - {Queues.VERDICT}")
    print(f"  - {Queues.RESULT}")
    print(f"  - {Queues.NOTIFICATION}")

    # Configure and run the Celery worker
    dsx_logging.info("Starting Celery worker for debugging...")
    init_worker() # necessary for when running in debug mode

    # Configure and run the Celery worker
    dsx_logging.info("Starting Celery worker for debugging...")
    celery_app.worker_main([
        "worker",
        "--loglevel=warning",
        "--pool=solo",  # <== allows for running in debugging mode.
        f"--queues={Queues.REQUEST},{Queues.VERDICT},{Queues.RESULT},{Queues.NOTIFICATION}",  #,{config.celery_app.encrypted_file_queue}
        "--concurrency=1"
    ])
