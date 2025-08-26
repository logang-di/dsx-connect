import pathlib
import json
from tinydb import TinyDB
from dsx_connect.models.scan_result import ScanStatsModel
from dsx_connect.database.scan_stats_base_db import ScanStatsBaseDB


class ScanStatsTinyDB(ScanStatsBaseDB):
    def __init__(self, db_path: str, collection_name: str = 'scan_stats'):
        super().__init__()
        self.db_path = db_path
        pathlib.Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self.db = TinyDB(db_path)
        self.collection = self.db.table(collection_name)

        if not self.collection:
            self.upsert(ScanStatsModel())

    def upsert(self, stats: ScanStatsModel):
        stats_dict = json.loads(stats.json())
        if self.collection:
            doc_id = self.collection.all()[0].doc_id
            self.collection.update(stats_dict, doc_ids=[doc_id])
        else:
            self.collection.insert(stats_dict)

    def get(self) -> ScanStatsModel:
        result = self.collection.all()
        return ScanStatsModel(**result[0]) if result else ScanStatsModel()

    def __len__(self):
        return len(self.collection)
