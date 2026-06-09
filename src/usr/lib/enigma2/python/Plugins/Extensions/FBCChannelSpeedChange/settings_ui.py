"""Settings screen wired into the standard Screens.Setup.Setup base.

Inheriting Setup gives the plugin three things for free:

  * the host image's setup skin (Metrix HD, Gradient FHD, OpenATV
    default, ...) — full HD/4K layout with title bar, config list on
    the left, description panel on the right, buttons at the bottom;
  * automatic per-row description text picked from the `description`
    attribute in `setup.xml`;
  * action-map wiring (red/green/menu/back) inherited from Setup so
    this module does not duplicate the button plumbing.

The only override is `keySave`: after `Setup.saveAll()` writes the
new values out, the controller is notified so a running session
picks them up without an enigma2 restart.
"""

from . import _
from .logger import error


class FBCChannelSpeedChangeSetup(object):
    """Lightweight wrapper, replaced at import time by the real Setup-
    derived class below. Kept as a fallback so off-box tests can import
    this module even when enigma2 is not on the path."""

    pass


try:
    from Screens.Setup import Setup

    class FBCChannelSpeedChangeSetup(Setup):  # noqa: F811 - intentional override
        def __init__(self, session):
            # PluginLanguageDomain is honoured on openatv-7.x Setup and
            # routes _() lookups through the plugin's gettext catalog so
            # the description texts surface translated. Older Setup
            # signatures simply do not accept the keyword; fall back to
            # the minimal call shape in that case.
            try:
                Setup.__init__(
                    self,
                    session,
                    setup="FBCChannelSpeedChange",
                    plugin="Extensions/FBCChannelSpeedChange",
                    PluginLanguageDomain="FBCChannelSpeedChange",
                )
            except TypeError:
                Setup.__init__(
                    self,
                    session,
                    setup="FBCChannelSpeedChange",
                    plugin="Extensions/FBCChannelSpeedChange",
                )
            self.setTitle(_("FBC ChannelSpeedChange"))

        def keySave(self):
            # Persist whatever the user touched. saveAll() returns False
            # if a value is still being edited (rare for ConfigYesNo) -
            # in that case Setup itself leaves the screen open.
            try:
                if not self.saveAll():
                    return
            except AttributeError:
                # Very old Setup builds expose only `.save()` on the
                # individual configs without a saveAll() helper.
                for _label, element in self["config"].list:
                    element.save()

            # Re-arm the live controller with the new toggles so the
            # user sees the change without restarting enigma2.
            try:
                from .controller import Controller
                ctrl = Controller.peek()
                if ctrl is not None:
                    ctrl.on_config_changed()
            except Exception as exc:
                error("on_config_changed crashed (caught): %r" % exc)

            self.close(True)

except Exception:
    # Off-box (tests) or enigma2 build without Screens.Setup: the
    # plugin keeps loading, but the settings screen is unreachable.
    # The plugin.open_setup wrapper already catches and logs that.
    pass


def open_setup(session, **kwargs):
    session.open(FBCChannelSpeedChangeSetup)
