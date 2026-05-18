import time
import os

from .config import cfg, LOG_PATH

_MAX_BYTES = 256 * 1024


def _truncate_if_large():
    try:
        if os.path.exists(LOG_PATH) and os.path.getsize(LOG_PATH) > _MAX_BYTES:
            os.remove(LOG_PATH)
    except OSError:
        pass


def log(level, msg):
    if level == "debug" and not cfg.debug_log.value:
        return
    _truncate_if_large()
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
