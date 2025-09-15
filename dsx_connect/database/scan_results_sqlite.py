import pathlib
import sqlite3
import json
from typing import List, Optional

from dsx_connect.database.scan_results_base_db import ScanResultsBaseDB
from dsx_connect.models.scan_result import ScanResultModel
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
                scan_job_id TEXT,
                metadata_tag TEXT,
                status TEXT NOT NULL,
                dpa_verdict TEXT  -- Stored as JSON
            )
        ''')
        self.connection.commit()
        # Best-effort migration for older databases missing scan_job_id
        try:
            self.cursor.execute("PRAGMA table_info(scan_results)")
            cols = [row[1] for row in self.cursor.fetchall()]
            if 'scan_job_id' not in cols:
                self.cursor.execute('ALTER TABLE scan_results ADD COLUMN scan_job_id TEXT')
                self.connection.commit()
        except Exception:
            pass

    def insert(self, model: ScanResultModel) -> int:
        if self._retain == 0:
            return  # Do nothing if retain is 0 (store nothing)

        """Insert a ScanResultModel into the database and return the doc_id."""
        verdict_json = model.verdict.json() if model.verdict else None
        # Pull scan_job_id from model (preferred) or nested scan_request if available
        scan_job_id = getattr(model, 'scan_job_id', None)
        if not scan_job_id:
            try:
                scan_job_id = getattr(model.scan_request, 'scan_job_id', None)
            except Exception:
                scan_job_id = None
        with self.lock:
            with self.connection:
                self.cursor.execute('''
                    INSERT INTO scan_results (scan_request_task_id, scan_job_id, metadata_tag, status, dpa_verdict)
                    VALUES (?, ?, ?, ?, ?)
                ''', (model.scan_request_task_id, scan_job_id, model.metadata_tag, model.status, verdict_json))


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

    def recent(self, limit: int = 200, job_id: Optional[str] = None) -> List[ScanResultModel]:
        with self.lock:
            if job_id:
                self.cursor.execute(
                    'SELECT * FROM scan_results WHERE scan_job_id = ? ORDER BY id DESC LIMIT ?',
                    (job_id, int(limit)),
                )
            else:
                self.cursor.execute('SELECT * FROM scan_results ORDER BY id DESC LIMIT ?', (int(limit),))
            rows = self.cursor.fetchall()
        return [self._row_to_model(row) for row in rows]

    def __len__(self):
        self.cursor.execute('SELECT COUNT(*) FROM scan_results')
        count = self.cursor.fetchone()[0]
        return count

    def _row_to_model(self, row):
        dpa_verdict = json.loads(row[5]) if row[5] else None
        return ScanResultModel(
            id=row[0],
            scan_request_task_id=row[1],
            scan_job_id=row[2],
            metadata_tag=row[3],
            status=row[4],
            verdict=dpa_verdict
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
