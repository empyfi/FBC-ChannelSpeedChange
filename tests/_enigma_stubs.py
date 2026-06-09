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

    mod.eTimer = eTimer
    mod.iPlayableService = iPlayableService
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
