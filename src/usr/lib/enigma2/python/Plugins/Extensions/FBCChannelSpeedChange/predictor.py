"""Predict which services to pre-tune.

For NEXT/PREV the predictor walks the currently active bouquet around
the live service. For HISTORY it consults
InfoBarChannelSelection.servicelist.history which core enigma2 already
maintains - no point shadowing it.

All lookups return ServiceReference-compatible objects (or None). They
must be cheap; the pool re-arms after every zap.
"""

from .logger import info, debug, warn


# eServiceReference flag for "is directory/marker, skip it" - matches
# the value used throughout enigma2 (eServiceReference::isDirectory or
# isMarker; filtered via flags & 0x47).
_SKIP_FLAGS = 0x47  # isDirectory | isMarker | mustDescent | canDescent


class Predictor:
    """Stateless oracle that reads from injected providers."""

    def __init__(self,
                 bouquet_provider=None,
                 service_center_provider=None,
                 history_provider=None,
                 current_service_provider=None):
        self._bouquet_provider = bouquet_provider or _default_bouquet_provider
        self._service_center_provider = service_center_provider or _default_service_center_provider
        self._history_provider = history_provider or _default_history_provider
        self._current_service_provider = current_service_provider or _default_current_service_provider

    # --- public API -----------------------------------------------------

    def next_service(self, count=1):
        """Return up to `count` services after the live one in the active bouquet."""
        return self._neighbors(direction=+1, count=count)

    def prev_service(self, count=1):
        """Return up to `count` services before the live one in the active bouquet."""
        return self._neighbors(direction=-1, count=count)

    def history_service(self, count=1):
        """Return up to `count` recently-watched services, most recent first,
        excluding the current live service.
        """
        live = self._current_service_provider()
        live_key = _key(live) if live is not None else None
        try:
            entries = list(self._history_provider() or [])
        except Exception as exc:
            warn("history_provider failed: %r" % exc)
            return []
        # Debug logging surfaces the history entry shape; nested-
        # tuple shapes vary across openatv builds and the recursive
        # _extract_ref handles them.
        if not entries:
            debug("PREDICT history: no entries from provider")
            return []
        debug("PREDICT history: %d entries, live=%s, first entry type=%s"
              % (len(entries), live_key, type(entries[0]).__name__))
        out = []
        seen = set()
        for entry in reversed(entries):  # most recent at the end in enigma2
            ref = _extract_ref(entry)
            if ref is None:
                debug("PREDICT history: skip - cannot extract ref from %r"
                      % (entry,))
                continue
            k = _key(ref)
            if k == live_key:
                debug("PREDICT history: skip live %s" % k)
                continue
            if k in seen:
                continue
            seen.add(k)
            out.append(ref)
            debug("PREDICT history: candidate %s" % k)
            if len(out) >= count:
                break
        if not out:
            info("PREDICT history: 0 candidates after filtering (entries=%d live=%s)"
                 % (len(entries), live_key))
        return out

    # --- internals ------------------------------------------------------

    def _neighbors(self, direction, count):
        if count <= 0:
            return []
        bouquet_ref = self._bouquet_provider()
        live = self._current_service_provider()
        if bouquet_ref is None or live is None:
            return []
        services = self._list_bouquet(bouquet_ref)
        if not services:
            return []
        live_key = _key(live)
        idx = next((i for i, s in enumerate(services) if _key(s) == live_key), None)
        if idx is None:
            debug("live service not in current bouquet; skipping next/prev")
            return []
        n = len(services)
        out = []
        step = 1 if direction > 0 else -1
        for offset in range(1, n):  # avoid infinite loop on tiny bouquets
            cand = services[(idx + step * offset) % n]
            if _key(cand) == live_key:
                break
            out.append(cand)
            if len(out) >= count:
                break
        return out

    def _list_bouquet(self, bouquet_ref):
        sc = self._service_center_provider()
        if sc is None:
            return []
        try:
            lister = sc.list(bouquet_ref)
            if lister is None:
                return []
            services = []
            ref = lister.getNext()
            while ref is not None and ref.valid():
                # eServiceReference exposes `flags` as a data attribute on
                # openatv builds, not as getFlags(). Fall back gracefully.
                try:
                    flags = ref.flags
                except AttributeError:
                    try:
                        flags = ref.getFlags()
                    except AttributeError:
                        flags = 0
                if not (flags & _SKIP_FLAGS):
                    services.append(ref)
                ref = lister.getNext()
            return services
        except Exception as exc:
            warn("bouquet list failed: %r" % exc)
            return []


# --- helpers ------------------------------------------------------------

def _key(ref):
    try:
        s = ref.toString()
    except AttributeError:
        s = str(ref)
    parts = s.split(":")
    if len(parts) >= 11:
        parts = parts[:10]
    return ":".join(parts)


def _tp_key(ref):
    """Transponder identity from a service-ref. Parts 4-6
    (tsid : onid : namespace) uniquely identify the transponder; the
    service id (part 3) is what differs between services on the same
    multiplex. eDVBResourceManager channel-shares at the transponder
    level, so two services with the same _tp_key share a demod lock
    once one is tuned - even when the full _key does not match.
    Returns an empty string for non-DVB / malformed refs.
    """
    try:
        s = ref.toString()
    except AttributeError:
        s = str(ref)
    parts = s.split(":")
    if len(parts) >= 7:
        return ":".join(parts[4:7])
    return ""


def _extract_ref(entry):
    """History entries vary across OpenATV versions:
        * plain eServiceReference
        * (bouquet_path, ref)
        * [bouquet_path_list, ref]
        * nested combinations of the above

    Walk recursively, in reverse order, so the trailing element (which
    is conventionally the actual service after a bouquet-path prefix)
    wins over anything earlier in the structure.
    """
    if entry is None:
        return None
    if hasattr(entry, "toString"):
        return entry
    if isinstance(entry, (list, tuple)):
        for item in reversed(entry):
            ref = _extract_ref(item)
            if ref is not None:
                return ref
    return None


# --- default providers -------------------------------------------------

def _default_bouquet_provider():
    """Return the ServiceReference of the active bouquet (TV or radio)."""
    try:
        # InfoBar.instance.servicelist holds the active bouquet root
        from Screens.InfoBar import InfoBar
        if InfoBar.instance and getattr(InfoBar.instance, "servicelist", None):
            return InfoBar.instance.servicelist.getRoot()
    except Exception:
        pass
    return None


def _default_service_center_provider():
    try:
        from enigma import eServiceCenter
        return eServiceCenter.getInstance()
    except Exception:
        return None


def _default_history_provider():
    try:
        from Screens.InfoBar import InfoBar
        if InfoBar.instance and getattr(InfoBar.instance, "servicelist", None):
            return InfoBar.instance.servicelist.history
    except Exception:
        pass
    return []


def _default_current_service_provider():
    try:
        import NavigationInstance
        nav = NavigationInstance.instance
        if nav is not None:
            return nav.getCurrentlyPlayingServiceReference()
    except Exception:
        pass
    return None
