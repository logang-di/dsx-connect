from dsx_connect.database.scan_stats_collection import ScanStatsCollection
from dsx_connect.database.scan_stats_tinydb import ScanStatsTinyDB
from dsx_connect.config import ConfigDatabaseType
from dsx_connect.database.scan_results_collection import ScanResultsCollection
from dsx_connect.database.scan_results_mongodb import ScanResultsMongoDB
from dsx_connect.database.scan_results_sqlite import ScanResultsSQLiteDB
from dsx_connect.database.scan_results_tinydb import ScanResultsTinyDB
from dsx_connect.utils.app_logging import dsx_logging


def database_scan_results_factory(database_type: str = 'tinydb',
                                  database_loc: str = 'data',
                                  retain: int = -1,
                                  collection_name: str = 'scan_results'):
    scan_results_db = None
    if database_type == ConfigDatabaseType.TINYDB:
        scan_results_db = ScanResultsTinyDB(database_loc, collection_name=collection_name, retain=retain)
        dsx_logging.debug(f'Scan results TinyDB database initialized at: {database_loc} Retention policy: {retain}')
    elif database_type == ConfigDatabaseType.SQLITE3:
        scan_results_db = ScanResultsSQLiteDB(database_loc,
                                              collection_name=collection_name,
                                              retain=retain)
        dsx_logging.debug(f'Scan results SQLite3 database initialized at: {database_loc} Retention policy: {retain}')
    elif database_type == ConfigDatabaseType.MONGODB:
        loc, db_name = database_loc.rsplit('/', 1)
        scan_results_db = ScanResultsMongoDB(loc, db_name=db_name, collection_name=collection_name, retain=retain)
        dsx_logging.debug(f'Scan results Mongo database initialized at: {database_loc} Retention policy: {retain}')
    else:
        scan_results_db = ScanResultsCollection(retain=retain)
        dsx_logging.debug(f'Scan results collection in memory. Retention policy: {retain}')

    return scan_results_db


def database_scan_stats_factory(database_type: str = 'tinydb',
                                database_loc: str = 'data',
                                collection_name: str = 'scan_stats'):
    scan_stats_db = None
    if database_type == ConfigDatabaseType.TINYDB:
        scan_stats_db = ScanStatsTinyDB(database_loc, collection_name=collection_name)
        dsx_logging.debug(f'Scan stats TinyDB database initialized at: {database_loc}')
    else:
        scan_stats_db = ScanStatsCollection()
        dsx_logging.debug(f'Scan stats collection in memory.')

    return scan_stats_db
