import pathlib
from typing import Optional, List

from tinydb import TinyDB, Query
import json

from dsx_connect.database.scan_results_base_db import ScanResultsBaseDB
from shared.dsx_logging import dsx_logging
from dsx_connect.models.scan_models import ScanResultModel, ScanResultStatusEnum


class ScanResultsTinyDB(ScanResultsBaseDB):
    def __init__(self, db_path: str, collection_name: str = 'scan_results', retain: int = -1):
        super().__init__(retain)
        self.db_path = db_path
        pathlib.Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self.db = TinyDB(db_path, encoding='utf-8')
        self.collection = self.db.table(collection_name)

    def __str__(self) -> str:
        return f'db: {self.db_path}   collection: {self.collection.name}'

    def insert(self, model: ScanResultModel) -> int:
        if self._retain == 0:
            dsx_logging.debug('(Retention set to 0, storing nothing)')
            return -1  # Do nothing if retain is 0 (store nothing)

        # Exclude the 'id' field when inserting, as TinyDB will assign a doc_id
        doc_id = self.collection.insert(json.loads(model.json(exclude={"id"})))
        model.id = doc_id  # Update the model with the assigned doc_id
        self._check_retain_limit()  # Enforce retention limit
        return doc_id

    def delete(self, key: str, value: str) -> bool:
        scan = Query()
        if key == 'id':
            doc_id = int(value)
            result = self.collection.remove(doc_ids=[doc_id])
        else:
            result = self.collection.remove(getattr(scan, key) == value)
        return bool(result)

    def delete_oldest(self) -> bool:
        if len(self.collection) > 0:
            oldest_record = self.collection.all()[0]
            self.collection.remove(doc_ids=[oldest_record.doc_id])
            return True
        return False

    def read_all(self) -> List[ScanResultModel]:
        return [ScanResultModel(id=item.doc_id, **item) for item in self.collection.all()]

    def find(self, key: str, value: str) -> Optional[List[ScanResultModel]]:
        # Handle search by 'id' (TinyDB doc_id)
        if key == 'id':
            result = self.collection.get(doc_id=int(value))
            results = [result] if result else []
        else:
            scan = Query()
            # Determine the type of the key in the database
            sample_item = self.collection.all()[0] if self.collection else None
            if sample_item:
                db_value_type = type(sample_item.get(key))
                value = db_value_type(value)
            results = self.collection.search(getattr(scan, key) == value)

        return [ScanResultModel(id=result.doc_id, **result) for result in results]

    def __len__(self) -> int:
        return len(self.collection)  # Use TinyDB's len() for efficient counting


if __name__ == "__main__":
    # Clean up test databases
    for db_file in ['test1.json', 'test2.json', 'test3.json']:
        if pathlib.Path(db_file).exists():
            pathlib.Path(db_file).unlink()

    service = ScanResultsTinyDB('test1.json')

    # Insert sample records with the new field names
    service.insert(ScanResultModel(
        scan_request_task_id='A',
        metadata_tag='test-A',
        status=ScanResultStatusEnum.SCANNED
    ))
    service.insert(ScanResultModel(
        scan_request_task_id='B',
        metadata_tag='test-B',
        status=ScanResultStatusEnum.SCANNED
    ))
    service.insert(ScanResultModel(
        scan_request_task_id='B',
        metadata_tag='test-B',
        status=ScanResultStatusEnum.SCANNED
    ))
    service.insert(ScanResultModel(
        scan_request_task_id='B',
        metadata_tag='test-B',
        status=ScanResultStatusEnum.SCANNED
    ))
    service.insert(ScanResultModel(
        scan_request_task_id='C',
        metadata_tag='test-C',
        status=ScanResultStatusEnum.SCANNED
    ))

    # Read all records
    print("All records:")
    print