"""One-shot API discovery. Runs inside enigma2 where _enigma is loaded.

Writes a structured dump of every API surface the plugin depends on
to /tmp/fbc_csc.log. Gated on config.plugins.fbc_csc.debug_log so
production boots stay quiet.
"""

from .logger import info, error
from .config import cfg


_DONE = False


def run_once():
    global _DONE
    if _DONE:
        return
    _DONE = True
    if not cfg.debug_log.value:
        return
    try:
        _dump_all()
    except Exception as exc:
        error("diagnostic.run_once outer: %r" % exc)


def _section(title):
    info("=" * 8 + " DIAG: " + title + " " + "=" * 8)


def _members(obj, label, max_items=400):
    try:
        names = sorted(dir(obj))
    except Exception as exc:
        info("[%s] dir() failed: %r" % (label, exc))
        return
    info("[%s] %d names" % (label, len(names)))
    count = 0
    for n in names:
        if n.startswith("_"):
            continue
        if count >= max_items:
            info("[%s] ... truncated" % label)
            break
        count += 1
        try:
            v = getattr(obj, n)
        except Exception as exc:
            info("  %s -> <getattr error: %r>" % (n, exc))
            continue
        kind = type(v).__name__
        info("  %s [%s]" % (n, kind))


def _dump_all():
    _section("enigma module")
    try:
        import enigma
        info("enigma.__file__ = %s" % getattr(enigma, "__file__", "?"))
        info("enigma top-level names containing 'DVB' or 'Service':")
        for n in sorted(dir(enigma)):
            if any(k in n for k in ("DVB", "Service", "Channel", "Tune")):
                info("  %s" % n)
    except Exception as exc:
        error("import enigma failed: %r" % exc)
        return

    _section("eDVBResourceManager class")
    try:
        from enigma import eDVBResourceManager
        _members(eDVBResourceManager, "eDVBResourceManager(class)")
        try:
            rm = eDVBResourceManager.getInstance()
            _members(rm, "eDVBResourceManager(instance)")
        except Exception as exc:
            info("getInstance() failed: %r" % exc)
    except Exception as exc:
        error("eDVBResourceManager: %r" % exc)

    _section("eServiceReference")
    try:
        from enigma import eServiceReference
        _members(eServiceReference, "eServiceReference(class)")
        try:
            sr = eServiceReference()
            _members(sr, "eServiceReference()(instance)")
        except Exception as exc:
            info("eServiceReference() failed: %r" % exc)
        # Try also a real reference from the current playback.
        try:
            import NavigationInstance
            nav = NavigationInstance.instance
            if nav is not None:
                cur = nav.getCurrentlyPlayingServiceReference()
                if cur is not None:
                    info("currentServiceReference toString=%s" % cur.toString())
                    _members(cur, "currentServiceReference(live)")
        except Exception as exc:
            info("currentServiceReference failed: %r" % exc)
    except Exception as exc:
        error("eServiceReference: %r" % exc)

    _section("eDVBChannel")
    try:
        from enigma import eDVBChannel
        _members(eDVBChannel, "eDVBChannel(class)")
    except Exception as exc:
        info("eDVBChannel: %r" % exc)

    _section("NavigationInstance")
    try:
        import NavigationInstance
        _members(NavigationInstance.instance, "NavigationInstance.instance")
        try:
            _members(NavigationInstance.instance.RecordTimer,
                     "NavigationInstance.instance.RecordTimer")
        except Exception as exc:
            info("RecordTimer access failed: %r" % exc)
    except Exception as exc:
        error("NavigationInstance: %r" % exc)

    _section("nimmanager")
    try:
        from Components.NimManager import nimmanager
        slots = list(getattr(nimmanager, "nim_slots", []))
        info("nim_slots count = %d" % len(slots))
        for i, slot in enumerate(slots):
            info("--- slot %d ---" % i)
            _members(slot, "nim_slot[%d]" % i, max_items=80)
    except Exception as exc:
        error("nimmanager: %r" % exc)

    _section("InfoBar")
    try:
        from Screens.InfoBar import InfoBar
        if InfoBar.instance is not None:
            ib = InfoBar.instance
            info("InfoBar.instance type=%s" % type(ib).__name__)
            for attr in ("zapUp", "zapDown", "historyBack", "historyNext",
                         "servicelist", "session"):
                info("  hasattr(infobar, %s) = %s" % (attr, hasattr(ib, attr)))
            try:
                sl = ib.servicelist
                info("  servicelist type=%s" % type(sl).__name__)
                for attr in ("history", "getRoot", "getCurrent", "getCurrentSelection"):
                    info("    servicelist.%s -> %s" % (attr, hasattr(sl, attr)))
            except Exception as exc:
                info("servicelist inspection failed: %r" % exc)
        else:
            info("InfoBar.instance is None (deferred?)")
    except Exception as exc:
        error("InfoBar: %r" % exc)

    _section("eFCCServiceManager (built-in FCC infrastructure)")
    try:
        from enigma import eFCCServiceManager
        _members(eFCCServiceManager, "eFCCServiceManager(class)")
        try:
            inst = eFCCServiceManager.getInstance()
            info("eFCCServiceManager.getInstance() before bootstrap = %r" % inst)
        except Exception as exc:
            info("getInstance() failed: %r" % exc)
            inst = None

        # On OpenATV 7.6 the FCC singleton is dormant - the C++
        # symbols are linked but nothing initialises the manager.
        # Probe whether setFCCEnable can bootstrap the singleton as a
        # class method. No further FCC calls; the dump only reports
        # whether the manager becomes available.
        try:
            rc = eFCCServiceManager.setFCCEnable(True)
            info("eFCCServiceManager.setFCCEnable(True) returned %r" % rc)
        except Exception as exc:
            info("setFCCEnable(True) raised: %r" % exc)

        try:
            inst2 = eFCCServiceManager.getInstance()
            info("eFCCServiceManager.getInstance() AFTER bootstrap = %r" % inst2)
            if inst2 is not None:
                _members(inst2, "eFCCServiceManager(instance after bootstrap)")
                try:
                    info("isEnable() = %r" % inst2.isEnable())
                except Exception as exc:
                    info("isEnable() raised: %r" % exc)
        except Exception as exc:
            info("getInstance() after bootstrap failed: %r" % exc)
    except Exception as exc:
        error("eFCCServiceManager import: %r" % exc)

    _section("iRecordableService (class-level constants)")
    try:
        from enigma import iRecordableService
        _members(iRecordableService, "iRecordableService(class)")
    except Exception as exc:
        error("iRecordableService import: %r" % exc)

    _section("iPlayableService event constants")
    try:
        from enigma import iPlayableService
        _members(iPlayableService, "iPlayableService(class)")
    except Exception as exc:
        error("iPlayableService import: %r" % exc)

    _section("FCC config state")
    try:
        from Components.config import config
        # Try common FCC config paths
        for path in ("usage.fcc_enabled", "usage.fccEnabled", "plugins.fcc.enabled",
                     "usage.boot_fcc", "misc.fcc"):
            parts = path.split(".")
            cur = config
            try:
                for p in parts:
                    cur = getattr(cur, p)
                v = getattr(cur, "value", cur)
                info("  config.%s = %r" % (path, v))
            except AttributeError:
                info("  config.%s = <not present>" % path)
    except Exception as exc:
        error("FCC config probe: %r" % exc)

    _section("end of diag")
