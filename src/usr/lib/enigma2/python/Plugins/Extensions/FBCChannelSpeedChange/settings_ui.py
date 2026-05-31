from Screens.Screen import Screen
from Components.ActionMap import ActionMap
from Components.ConfigList import ConfigListScreen
from Components.config import getConfigListEntry

from . import _
from .config import cfg


class FBCChannelSpeedChangeSetup(ConfigListScreen, Screen):
    skin = """
    <screen name="FBCChannelSpeedChangeSetup" position="center,center" size="600,400" title="FBC ChannelSpeedChange">
        <widget name="config" position="10,10" size="580,340" scrollbarMode="showOnDemand"/>
        <ePixmap pixmap="skin_default/buttons/red.png" position="10,360" size="140,40" alphatest="blend"/>
        <ePixmap pixmap="skin_default/buttons/green.png" position="160,360" size="140,40" alphatest="blend"/>
        <widget name="key_red" position="10,360" size="140,40" font="Regular;20" halign="center" valign="center" foregroundColor="white" backgroundColor="background" zPosition="1" transparent="1"/>
        <widget name="key_green" position="160,360" size="140,40" font="Regular;20" halign="center" valign="center" foregroundColor="white" backgroundColor="background" zPosition="1" transparent="1"/>
    </screen>
    """

    def __init__(self, session):
        Screen.__init__(self, session)
        self.setTitle("FBC ChannelSpeedChange")

        entries = [
            getConfigListEntry(_("Enable plugin"), cfg.enabled),
            getConfigListEntry(_("Allow tuner allocation (master safety)"), cfg.allow_pretune),
            getConfigListEntry(_("Use real pre-tune (prepare+start, faster)"), cfg.use_real_pretune),
            getConfigListEntry(_("Pre-tune NEXT channel"), cfg.pretune_next),
            getConfigListEntry(_("Pre-tune PREVIOUS channel"), cfg.pretune_prev),
            getConfigListEntry(_("Pre-tune LAST channel (history)"), cfg.pretune_history),
            getConfigListEntry(_("Pre-warm pay-TV descrambler: LAST"), cfg.prewarm_descrambler_history),
            getConfigListEntry(_("Pre-warm pay-TV descrambler: NEXT"), cfg.prewarm_descrambler_next),
            getConfigListEntry(_("Pre-warm pay-TV descrambler: PREVIOUS"), cfg.prewarm_descrambler_prev),
            getConfigListEntry(_("Release demods when recording starts"), cfg.release_for_recording),
            getConfigListEntry(_("Release demods when PiP starts"), cfg.release_for_pip),
            getConfigListEntry(_("Show zap latency OSD"), cfg.show_osd_timing),
            getConfigListEntry(_("Verbose debug logging"), cfg.debug_log),
        ]

        ConfigListScreen.__init__(self, entries, session=session)

        from Components.Label import Label
        self["key_red"] = Label(_("Cancel"))
        self["key_green"] = Label(_("Save"))

        self["actions"] = ActionMap(
            ["SetupActions", "ColorActions"],
            {
                "save": self._save,
                "green": self._save,
                "cancel": self._cancel,
                "red": self._cancel,
                "ok": self._save,
            },
            -2,
        )

    def _save(self):
        for _, element in self["config"].list:
            element.save()
        from .controller import Controller
        ctrl = Controller.peek()
        if ctrl is not None:
            ctrl.on_config_changed()
        self.close(True)

    def _cancel(self):
        for _, element in self["config"].list:
            element.cancel()
        self.close(False)


def open_setup(session, **kwargs):
    session.open(FBCChannelSpeedChangeSetup)
