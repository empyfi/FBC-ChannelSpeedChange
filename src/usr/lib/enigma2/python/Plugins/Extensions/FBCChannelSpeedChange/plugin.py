"""FBC-ChannelSpeedChange plugin entry point.

Two PluginDescriptor registrations:
  * WHERE_SESSIONSTART : auto-init the Controller when a session starts.
  * WHERE_PLUGINMENU   : open the Settings UI from the Plugin browser.

Init is wrapped in try/except so a failure anywhere in this plugin
cannot bring down enigma2.
"""

from Plugins.Plugin import PluginDescriptor

from .logger import info, error
from .config import PLUGIN_NAME

_controller = None


def session_start(reason, **kwargs):
    """WHERE_SESSIONSTART entry. Called once per session.

    `reason == 0` means session is starting; `reason == 1` means it is
    shutting down. Both are honoured for clean teardown.
    """
    global _controller
    try:
        session = kwargs.get("session")
        if reason == 0:
            if session is None:
                error("session_start without session; aborting")
                return
            from .controller import Controller
            _controller = Controller.get(session)
            _controller.start()
            info("%s session_start ok" % PLUGIN_NAME)
        elif reason == 1 and _controller is not None:
            _controller.stop()
            _controller = None
            info("%s session_end ok" % PLUGIN_NAME)
    except Exception as exc:
        error("session_start crashed (caught): %r" % exc)


def open_setup(session, **kwargs):
    try:
        from .settings_ui import open_setup as do_open
        do_open(session, **kwargs)
    except Exception as exc:
        error("open_setup crashed (caught): %r" % exc)


def Plugins(**kwargs):
    return [
        PluginDescriptor(
            name="FBC ChannelSpeedChange",
            description="Accelerate channel zapping using FBC pre-tune",
            where=PluginDescriptor.WHERE_SESSIONSTART,
            needsRestart=False,
            fnc=session_start,
        ),
        PluginDescriptor(
            name="FBC ChannelSpeedChange",
            description="Settings for the FBC zap accelerator",
            where=PluginDescriptor.WHERE_PLUGINMENU,
            icon="plugin.png",
            fnc=open_setup,
        ),
    ]
