from abc import ABC, abstractmethod

from pydantic import BaseModel
from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer
import pathlib
from dsx_connect.utils.app_logging import dsx_logging


class ScanFolderModel(BaseModel):
    folder: pathlib.Path
    recursive: bool = True
    scan_existing: bool = True


class FilesystemMonitorCallback(ABC):
    @abstractmethod
    def file_modified_callback(self, file_path: pathlib.Path):
        pass


class FilesystemMonitor(FileSystemEventHandler):

    def __init__(self, monitor_folder: ScanFolderModel, callback: FilesystemMonitorCallback):
        self._observer = None
        self._monitor_folder = monitor_folder
        self._scheduled_observers = []
        self._callback = callback
        self._initialized = False

    @property
    def monitor_folder(self) -> ScanFolderModel:
        return self._monitor_folder

    def start(self):
        # event_handlers = [MyHandler(path, dpa_client) for path in scan_list]
        # observers = [Observer() for _ in scan_list]
        # for observer, event_handler, path in zip(observers, event_handlers, scan_list):
        # scan anything that already exists in that folder that
        self._observer = Observer()

        # in the even that we've got watchers still scheduled (if this app died), unschedule them
        if not self._initialized:
            self._observer.unschedule_all()
            self._initialized = True

        if self.monitor_folder.folder in self._scheduled_observers:
            dsx_logging.debug(f'{self.monitor_folder.folder} already being observed')
            return

        self._observer.schedule(self, self.monitor_folder.folder, recursive=self._monitor_folder.recursive)
        self._scheduled_observers.append(self.monitor_folder.folder)
        self._observer.start()

    def stop(self):
        if self._observer:
            self._observer.stop()
            self._observer.join()

    def on_modified(self, event):
        if event.is_directory:
            return

        filename = pathlib.Path(event.src_path)
        content = b''
        if filename.exists() and filename.is_file():  # yes, I know we are checking for directory above, but just
            # double-checking that the file is there and is a file.
            # self._lock.acquire()
            try:
                with filename.open('rb') as file:
                    # TODO - we dont need the entire content... just enough to see that we can
                    content = file.read(1)
            # this may seem overly cautious, but the nature of watchdog.on_modified, is that a file
            # being currently written or deleted may trigger an on_modified, and by the time we start
            # reading the file, it could be in a different state.
            # Unfortunately, I have not been successful using watchdog.on_close across platforms
            # (well documented online the issues with multiplatform monitoring)
            except FileNotFoundError as e:
                dsx_logging.warning(f'File {event.src_path} not found: {e}')
                return
            except Exception as e:  # file probably isn't quite ready to be read yet, on Windows in particular this
                # happens because the file hasn't been completely closed yet
                dsx_logging.debug(f'File {event.src_path} not ready to open: {e}')
                return
            finally:
                pass
                # self._lock.release()

        dsx_logging.info(f'New or modified file detected: {event.src_path}')
        self._callback.file_modified_callback(file_path=filename)

