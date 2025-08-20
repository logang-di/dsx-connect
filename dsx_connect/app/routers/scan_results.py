from typing import List

from fastapi import APIRouter

from dsx_connect.models.scan_models import ScanResultModel, ScanStatsModel
from dsx_connect.config import get_config
from shared.routes import DSXConnectAPI, API_PREFIX_V1, route_name, Action, ScanPath, route_path
from dsx_connect.database.database_factory import database_scan_stats_factory, database_scan_results_factory

router = APIRouter(prefix=route_path(API_PREFIX_V1))

config = get_config()
_results_database = database_scan_results_factory(config.database.type,
                                                  database_loc=config.database.loc,
                                                  retain=config.database.retain)

_stats_database = database_scan_stats_factory(database_loc=config.database.scan_stats_db)


@router.get(
    route_path(DSXConnectAPI.SCAN_PREFIX.value, ScanPath.RESULTS.value),
    name=route_name(DSXConnectAPI.SCAN_PREFIX, ScanPath.RESULTS, Action.LIST),
    response_model=List[ScanResultModel],
    description="List scan results."
)
async def list_scan_results() -> List[ScanResultModel]:
    return _results_database.read_all()


@router.get(
    route_path(DSXConnectAPI.SCAN_PREFIX.value, ScanPath.RESULTS.value, "{task_id}"),
    name=route_name(DSXConnectAPI.SCAN_PREFIX, ScanPath.RESULTS, Action.GET),
    response_model=List[ScanResultModel],
    description="List scan results."
)
async def get_scan_result(task_id: str) -> List[ScanResultModel]:
    return _results_database.find("scan_request_task_id", task_id)


@router.get(
    route_path(DSXConnectAPI.SCAN_PREFIX.value, ScanPath.STATS.value),
    name=route_name(DSXConnectAPI.SCAN_PREFIX, ScanPath.STATS, Action.GET),
    response_model=ScanStatsModel,
    description="Retrieve scan statistics.")
async def get_scan_stats() -> ScanStatsModel:
    return _stats_database.get()
