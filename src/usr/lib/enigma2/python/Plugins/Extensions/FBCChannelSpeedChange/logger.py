import time
import os

from .config import cfg, LOG_PATH

_MAX_BYTES = 256 * 1024
_BACKUP_COUNT = 3


def _rotate():
    """Rotate the log: drop the oldest backup, shift each `.N` to
    `.N+1`, and move the current log to `.1`. The next write recreates
    the live log empty. Keeping a few backups preserves the minutes
    leading up to a crash that a plain delete-on-overflow would lose.
    """
    oldest = "%s.%d" % (LOG_PATH, _BACKUP_COUNT)
    try:
        if os.path.exists(oldest):
            os.remove(oldest)
    except OSError:
        pass
    for i in range(_BACKUP_COUNT - 1, 0, -1):
        src = "%s.%d" % (LOG_PATH, i)
        dst = "%s.%d" % (LOG_PATH, i + 1)
        try:
            if os.path.exists(src):
                os.rename(src, dst)
        except OSError:
            pass
    try:
        if os.path.exists(LOG_PATH):
            os.rename(LOG_PATH, LOG_PATH + ".1")
    except OSError:
        pass


def _rotate_if_large():
    try:
        if os.path.exists(LOG_PATH) and os.path.getsize(LOG_PATH) > _MAX_BYTES:
            _rotate()
    except OSError:
        pass


def log(level, msg):
    if level == "debug" and not cfg.debug_log.value:
        return
    _rotate_if_large()
    line = "%s [%s] %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), level, msg)
    try:
        with open(LOG_PATH, "a") as fh:
            fh.write(line)
    except OSError:
        pass


def info(msg):
    log("info", msg)


def debug(msg):
    log("debug", msg)


def warn(msg):
    log("warn", msg)


def error(msg):
    log("error", msg)
