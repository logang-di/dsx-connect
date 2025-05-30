import pathlib
import sqlite3
import json
from typing import List, Optional

from dsx_connect.database.scan_results_base_db import ScanResultsBaseDB
from dsx_connect.models.scan_models import ScanResultModel
import threading


class ScanResultsSQLiteDB(ScanResultsBaseDB):
    def __init__(self, db_path: str, collection_name: str, retain: int = -1):
        super().__init__(retain)
        self.db_path = db_path
        pathlib.Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.db_path, check_same_thread=False)
        self.connection.execute("PRAGMA journal_mode=WAL")  # Enable WAL for better concurrency
        self.cursor = self.connection.cursor()
        self.create_table()
        self.lock = threading.Lock()  # Lock to manage write operations

    def create_table(self):
        """Create the scan_results table if it doesn't exist."""
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS scan_results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                scan_request_task_id TEXT NOT NULL,
                metadata_tag TEXT,
                status TEXT NOT NULL,
                dpa_verdict TEXT  -- Stored as JSON
            )
        ''')
        self.connection.commit()

    def insert(self, model: ScanResultModel) -> int:
        if self._retain == 0:
            return  # Do nothing if retain is 0 (store nothing)

        """Insert a ScanResultModel into the database and return the doc_id."""
        verdict_json = model.dpa_verdict.json() if model.dpa_verdict else None
        with self.lock:
            with self.connection:
                self.cursor.execute('''
                    INSERT INTO scan_results (scan_request_task_id, metadata_tag, status, dpa_verdict)
                    VALUES (?, ?, ?, ?)
                ''', (model.scan_request_task_id, model.metadata_tag, model.status, verdict_json))


                self._check_retain_limit()
                return self.cursor.lastrowid

    def delete(self, doc_id: int) -> bool:
        with self.lock:
            with self.connection:
                self.cursor.execute('DELETE FROM scan_results WHERE id = ?', (doc_id,))
                return self.cursor.rowcount > 0

    def delete_oldest(self):
        self.cursor.execute('DELETE FROM scan_results WHERE id = (SELECT id FROM scan_results ORDER BY id LIMIT 1)')
        self.connection.commit()

    def read_all(self) -> List[ScanResultModel]:
        with self.lock:
            self.cursor.execute('SELECT * FROM scan_results')
            rows = self.cursor.fetchall()
        return [self._row_to_model(row) for row in rows]

    def find(self, key: str, value) -> Optional[List[ScanResultModel]]:
        query = f"SELECT * FROM scan_results WHERE {key} = ?"
        self.cursor.execute(query, (value,))
        rows = self.cursor.fetchall()
        return [self._row_to_model(row) for row in rows] if rows else None

    def __len__(self):
        self.cursor.execute('SELECT COUNT(*) FROM scan_results')
        count = self.cursor.fetchone()[0]
        return count

    def _row_to_model(self, row):
        dpa_verdict = json.loads(row[4]) if row[4] else None
        return ScanResultModel(
            id=row[0],
            scan_request_task_id=row[1],
            metadata_tag=row[2],
            status=row[3],
            dpa_verdict=dpa_verdict
        )



# Example Usage
if __name__ == "__main__":
    if pathlib.Path('test1.db').exists():
        pathlib.Path('test1.db').unlink()
    if pathlib.Path('test2.db').exists():
        pathlib.Path('test2.db').unlink()
    if pathlib.Path('test3.db').exists():
        pathlib.Path('test3.db').unlink()

    db = ScanResultsSQLiteDB('test1.db', collection_name='scan_results')

    # Insert sample records
    db.insert(ScanResultModel(scan_request_task_id='A'))
    db.insert(ScanResultModel(scan_request_task_id='B', quarantined=True))

    # Read all records
    print("All records:")
    print(db.read_all())

    # Find specific records
    print("\nRecords matching 'scan_request_task_id=B':")
    matching_records = db.find("scan_request_task_id", 'B')
    print(matching_records)

    # Delete record by id
    print("\nDeleting record with id=2:")
    db.delete(2)
    print(db.read_all())

    testdb = ScanResultsSQLiteDB('test2.db', collection_name='scan_results', retain=5)
    testdb.insert(ScanResultModel(scan_request_task_id='A'))
    testdb.insert(ScanResultModel(scan_request_task_id='B'))
    testdb.insert(ScanResultModel(scan_request_task_id='B'))
    testdb.insert(ScanResultModel(scan_request_task_id='B'))
    testdb.insert(ScanResultModel(scan_request_task_id='C'))
    testdb.insert(ScanResultModel(scan_request_task_id='A'))
    testdb.insert(ScanResultModel(scan_request_task_id='B'))
    testdb.insert(ScanResultModel(scan_request_task_id='B'))
    testdb.insert(ScanResultModel(scan_request_task_id='B'))
    testdb.insert(ScanResultModel(scan_request_task_id='C'))

    print(f'length: {len(testdb)}')

    print("All records:")
    print(testdb.read_all())

    # don't retain anything
    testdb = ScanResultsSQLiteDB('test3.db', collection_name='scan_results', retain=0)
    testdb.insert(ScanResultModel(scan_request_task_id='A'))
    testdb.insert(ScanResultModel(scan_request_task_id='B'))
    testdb.insert(ScanResultModel(scan_request_task_id='B'))
    testdb.insert(ScanResultModel(scan_request_task_id='B'))

    print(f'length: {len(testdb)}')
