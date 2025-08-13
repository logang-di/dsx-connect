#!/usr/bin/env python3
"""
Debug script to inspect and test DLQ requeuing
"""

import json
import sys
from dsx_connect.utils.redis_manager import redis_manager, RedisQueueNames
from dsx_connect.config import ConfigManager

def inspect_dlq_items():
    """Inspect items in the DLQ to understand their structure"""
    print("=== DLQ INSPECTION ===")

    # Get all queue stats
    stats = redis_manager.get_all_dead_letter_stats()

    for queue_name, queue_stats in stats.items():
        print(f"\nQueue: {queue_name}")
        print(f"Length: {queue_stats.get('length', 0)}")

        if queue_stats.get('length', 0) > 0:
            try:
                # Get the enum for this queue
                queue_enum = RedisQueueNames[queue_name]
                items_data = redis_manager.get_dead_letter_items(queue_enum, 0, 3)

                print(f"Sample items from {queue_name}:")
                for i, item in enumerate(items_data.get('items', [])):
                    print(f"\n--- Item {i+1} ---")
                    print(f"Type: {type(item)}")

                    if isinstance(item, dict):
                        print(f"Keys: {list(item.keys())}")

                        # Check if it's a DeadLetterItem
                        if 'scan_request' in item:
                            print("Format: DeadLetterItem")
                            scan_request = item['scan_request']
                            print(f"  Failure reason: {item.get('failure_reason')}")
                            print(f"  Location: {scan_request.get('location')}")
                            print(f"  Connector URL: {scan_request.get('connector_url')}")
                            print(f"  Scan request keys: {list(scan_request.keys())}")
                        elif 'location' in item:
                            print("Format: Direct ScanRequest")
                            print(f"  Location: {item.get('location')}")
                            print(f"  Connector URL: {item.get('connector_url')}")
                        else:
                            print("Format: Unknown")
                            print(f"  Full item: {item}")
                    else:
                        print(f"Raw content: {str(item)[:200]}...")

            except Exception as e:
                print(f"Error inspecting {queue_name}: {e}")


def test_requeue_single_item():
    """Test requeuing a single item to see what task name is sent"""
    print("\n=== TESTING SINGLE REQUEUE ===")

    config = ConfigManager.get_config()

    # Get one item from DLQ
    queue_enum = RedisQueueNames.DLQ_SCAN_FILE
    items_data = redis_manager.get_dead_letter_items(queue_enum, 0, 1)

    if not items_data.get('items'):
        print("No items in DLQ to test with")
        return

    item = items_data['items'][0]
    print(f"Test item: {item}")

    # Test what task name we would send
    task_name = config.taskqueue.scan_request_task
    print(f"Task name from config: {task_name}")

    # Extract scan_request
    if 'scan_request' in item:
        scan_request = item['scan_request']
        print(f"Extracted scan_request: {scan_request.get('location')}")
        print(f"Would send task: {task_name}")
        print(f"Would send args: [{scan_request}]")
        print(f"Would send to queue: {config.taskqueue.scan_request_queue}")
    else:
        print("No scan_request found in item")


def manual_requeue_test():
    """Manually test sending a task to see if it works"""
    print("\n=== MANUAL TASK TEST ===")

    from dsx_connect.celery_app.celery_app import celery_app
    config = ConfigManager.get_config()

    # Create a test scan request
    test_scan_request = {
        "location": "test_file.txt",
        "metainfo": "test_file.txt",
        "connector_url": "http://test.example.com"
    }

    task_name = config.taskqueue.scan_request_task
    print(f"Sending test task: {task_name}")
    print(f"Args: {test_scan_request}")

    try:
        result = celery_app.send_task(
            name=task_name,
            args=[test_scan_request],
            queue=config.taskqueue.scan_request_queue
        )
        print(f"Task sent successfully: {result.id}")
        print(f"Task name sent: {task_name}")
        print("Check your celery worker to see if it receives this task properly")
    except Exception as e:
        print(f"Failed to send test task: {e}")


if __name__ == "__main__":
    if len(sys.argv) > 1:
        cmd = sys.argv[1]
        if cmd == "inspect":
            inspect_dlq_items()
        elif cmd == "test-single":
            test_requeue_single_item()
        elif cmd == "test-manual":
            manual_requeue_test()
        else:
            print("Unknown command")
    else:
        print("Available commands:")
        print("  python debug_dlq.py inspect      - Inspect DLQ items")
        print("  python debug_dlq.py test-single  - Test single requeue")
        print("  python debug_dlq.py test-manual  - Manual task test")
        print()
        inspect_dlq_items()