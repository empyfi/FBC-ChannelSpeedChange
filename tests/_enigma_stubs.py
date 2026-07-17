"""Minimal stubs for enigma2 modules so the plugin can be imported off-box.

Only what the plugin imports at module load time is stubbed here.
Runtime provider calls are dependency-injected and overridden per
test - no need to fake the full enigma API.
"""

import sys
import types


def _build_components_config():
    pkg = types.ModuleType("Components")
    pkg.__path__ = []
    cfg_mod = types.ModuleType("Components.config")

    class _ConfigValue:
        def __init__(self, default):
            self._default = default
            self.value = default

        def save(self):
            pass

        def cancel(self):
            self.value = self._default

    class ConfigYesNo(_ConfigValue):
        pass

    class ConfigInteger(_ConfigValue):
        def __init__(self, default, limits=None):
            super().__init__(default)
            self.limits = limits

    class ConfigSelection(_ConfigValue):
        def __init__(self, default, choices):
            super().__init__(default)
            self.choices = choices

    class ConfigNothing:
        """Non-editable placeholder used by setup.xml group separators."""

        value = None

        def save(self):
            pass

        def cancel(self):
            pass

    class ConfigSubsection:
        pass

    class _Plugins:
        pass

    class _Config:
        def __init__(self):
            self.plugins = _Plugins()

    cfg_mod.config = _Config()
    cfg_mod.ConfigSubsection = ConfigSubsection
    cfg_mod.ConfigYesNo = ConfigYesNo
    cfg_mod.ConfigInteger = ConfigInteger
    cfg_mod.ConfigSelection = ConfigSelection
    cfg_mod.ConfigNothing = ConfigNothing
    sys.modules["Components"] = pkg
    sys.modules["Components.config"] = cfg_mod
    return cfg_mod


def _build_enigma():
    mod = types.ModuleType("enigma")

    class _CallbackList(list):
        def append(self, cb):
            list.append(self, cb)

    class eTimer:
        def __init__(self):
            self.callback = _CallbackList()
            self._interval = None
            self._single_shot = False
            self._running = False

        def start(self, interval_ms, single_shot=False):
            self._interval = interval_ms
            self._single_shot = single_shot
            self._running = True

        def stop(self):
            self._running = False

    class iPlayableService:
        evTunedIn = 6  # arbitrary; tests don't depend on value
        evStart = 1    # ditto
        evNewProgramInfo = 7  # ditto - used by controller external slot

    class eServiceReference:
        """Minimal stand-in for the SWIG class - the api module's
        string-coerce path constructs one to hand off to the
        controller, tests assert on ``.toString()`` equality.

        ``type`` mirrors the SWIG property; parsed from the first
        colon-separated field of the ref string so ``"1:0:..."``
        yields ``type=1`` (DVB) and ``"4097:0:..."`` yields
        ``type=4097`` (IPTV / stream). Defaults to 1 for empty or
        malformed strings so tests that pass an arbitrary label get
        DVB semantics without extra setup.
        """

        def __init__(self, s=""):
            self._s = str(s)
            try:
                self.type = int(self._s.split(":", 1)[0])
            except (ValueError, IndexError):
                self.type = 1

        def toString(self):
            return self._s

        def __eq__(self, other):
            return (isinstance(other, eServiceReference)
                    and self._s == other._s)

        def __hash__(self):
            return hash(self._s)

        def __repr__(self):
            return "eServiceReference(%r)" % self._s

    class pNavigation:
        """RecordType bitmask enum, mirrors openatv 7.6 lib/nav/core.h.

        Verified on-box 2026-06-25 via diagnostic.py dump. Tests that
        want to simulate an older build with the FastZap constant
        missing should ``delattr(pNavigation, 'isFromSpecialJumpFastZap')``
        before reloading the plugin's config module.
        """
        isRealRecording          = 1
        isStreaming              = 2
        isPseudoRecording        = 4
        isUnknownRecording       = 8
        isFromTimer              = 16
        isFromInstantRecording   = 32
        isFromEPGrefresh         = 64
        isFromSpecialJumpFastZap = 128
        isAnyRecording           = 255

    mod.eTimer = eTimer
    mod.iPlayableService = iPlayableService
    mod.eServiceReference = eServiceReference
    mod.pNavigation = pNavigation
    sys.modules["enigma"] = mod
    return mod


def install():
    if "Components.config" not in sys.modules:
        _build_components_config()
    if "enigma" not in sys.modules:
        _build_enigma()


def plugin_path():
    """Return the path that must be added to sys.path so
    `from FBCChannelSpeedChange import ...` works.
    """
    import os
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.normpath(os.path.join(
        here, "..", "src", "usr", "lib", "enigma2", "python", "Plugins", "Extensions"
    ))


def bootstrap():
    install()
    p = plugin_path()
    if p not in sys.path:
        sys.path.insert(0, p)
