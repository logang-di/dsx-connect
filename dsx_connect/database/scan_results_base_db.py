from abc import ABC, abstractmethod

from dsx_connect.models.scan_result import ScanResultModel


class ScanResultsBaseDB(ABC):
    def __init__(self, retain: int = -1):
        self._retain = retain

    @abstractmethod
    def read_all(self) -> list[ScanResultModel]:
        """Read all data from the JSON file."""
        pass

    @abstractmethod
    def insert(self, scan_result: ScanResultModel):
        """Insert a new record into the JSON file."""
        pass

    @abstractmethod
    def delete(self, key, value) -> ScanResultModel:
        """Delete a record from the JSON file based on a key-value pair."""
        pass

    @abstractmethod
    def delete_oldest(self):
        """Delete the oldest record.  Typically used in conjunction with record retention limit maintain a
        specific record count."""
        pass

    @abstractmethod
    def find(self, key, value) -> list[ScanResultModel] | None:
        """Find records in the JSON file based on a key-value pair."""
        pass

    @abstractmethod
    def __len__(self) -> int:
        """Return the number of records in the database."""
        pass

    def _check_retain_limit(self):
        """Check if the retain limit is exceeded and delete the oldest record if necessary."""
        if self._retain > 0 and len(self) > self._retain:
            self.delete_oldest()
