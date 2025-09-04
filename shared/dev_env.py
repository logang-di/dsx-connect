import os
from pathlib import Path
from typing import Optional

_DEVEVN_LOGGED = False


def load_devenv(default_path: Optional[Path] = None,
                env_var: str = "DSXCONNECTOR_ENV_FILE") -> None:
    """
    Lightweight loader for a development-only env file.

    - If env var `DSXCONNECTOR_ENV_FILE` is set, use that path.
    - Else, if `default_path` is provided and exists, use it.
    - Parses simple KEY=VALUE lines, ignores blanks/comments.
    - Populates os.environ ONLY for keys that are not already set.
    """
    path_str = os.getenv(env_var)
    path: Optional[Path] = Path(path_str) if path_str else (default_path if default_path else None)
    if not path:
        return
    try:
        if not path.exists():
            return
        from shared.dsx_logging import dsx_logging
        global _DEVEVN_LOGGED
        if not _DEVEVN_LOGGED:
            # Reduce chatter: log only once and at debug level
            dsx_logging.debug(f"Loading dev env from {path}")
            _DEVEVN_LOGGED = True
        for line in path.read_text().splitlines():
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            if "=" not in s:
                continue
            key, val = s.split("=", 1)
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = val
    except Exception:
        # Silent failure: dev convenience only
        return
