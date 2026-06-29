"""Settings screen wired into the standard Screens.Setup.Setup base.

Inheriting Setup gives the plugin three things for free:

  * the host image's setup skin (Metrix HD, Gradient FHD, OpenATV
    default, ...) — full HD/4K layout with title bar, config list on
    the left, description panel on the right, buttons at the bottom;
  * automatic per-row description text picked from the `description`
    attribute in `setup.xml`;
  * action-map wiring (red/green/menu/back) inherited from Setup so
    this module does not duplicate the button plumbing.

The only override is `keySave`: the live controller is notified
before delegating to the parent's full save+close path so a running
session picks the new values up without an enigma2 restart.

A small post-init pass injects an FCC-Extender presence indicator
into the External pretune group so the user can see at a glance
whether the companion plugin is on the box.
"""

from . import _
from .logger import error


_OPKG_STATUS_PATH = "/var/lib/opkg/status"


def _parse_fccextender_status(content):
    """Low-level opkg-status parser. Returns ``(state, version)``
    where ``state`` is ``"installed"`` or ``"not_detected"`` and
    ``version`` is the matched ``Version:`` string or ``None``.

    Splits the body on blank lines (opkg-status block separator).
    Each block carries ``Package:`` and ``Version:`` key-value
    lines. A ``Package:`` value with an ``fccextender`` /
    ``fcc-extender`` stem (substring match, case-insensitive) ends
    the search.

    The VTi build's exact name is
    ``enigma2-plugin-extensions-vti-fccextender``; the OpenATV
    variant published by Oberhesse keeps the ``fccextender`` stem
    (``enigma2-plugin-extensions-fccextender``). The substring rule
    catches either spelling and any future stem variant.
    """
    for block in content.split("\n\n"):
        package_name = None
        version = None
        for line in block.splitlines():
            if line.startswith("Package:"):
                package_name = line.split(":", 1)[1].strip()
            elif line.startswith("Version:"):
                version = line.split(":", 1)[1].strip()
        if not package_name:
            continue
        lowered = package_name.lower()
        if "fccextender" not in lowered and "fcc-extender" not in lowered:
            continue
        return ("installed", version)
    return ("not_detected", None)


def _classify_fccextender_content(content):
    """Render an opkg-status file body into the human-readable
    presence-indicator label injected into the setup list.
    """
    state, version = _parse_fccextender_status(content)
    if state == "installed":
        if version:
            return _("FCC-Extender: installed (v%s)") % version
        return _("FCC-Extender: installed")
    return _("FCC-Extender: not detected")


def _detect_fccextender_status():
    """Read the opkg status file and return the user-facing label.

    Wrapped in try/except so an unexpected I/O failure surfaces as
    "status unknown" rather than crashing the setup screen open.
    """
    try:
        with open(_OPKG_STATUS_PATH, "r") as fh:
            content = fh.read()
        return _classify_fccextender_content(content)
    except Exception:
        return _("FCC-Extender: status unknown")


def _fccextender_installed():
    """Boolean detection used by the yellow-button shortcut. Reads
    the same opkg-status file as the presence indicator so the
    button is gated on the same evidence that surfaces the label.

    An I/O failure collapses to ``False`` - safer to omit the
    button than to render a shortcut that the click handler would
    silently fail on.
    """
    try:
        with open(_OPKG_STATUS_PATH, "r") as fh:
            content = fh.read()
    except Exception:
        return False
    state, _version = _parse_fccextender_status(content)
    return state == "installed"


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
            self._inject_fccextender_status()
            self._wire_fccextender_shortcut()

        def _inject_fccextender_status(self):
            """Insert a status row right after the External pretune
            group header so the user sees the FCC-Extender presence
            without leaving the screen.

            Setup's rendered list holds header-style rows as 1-tuples
            and config rows as 3-tuples. Inserting a fresh 1-tuple
            matches the Setup base class's "label only" branch and
            renders as a non-selectable info row.
            """
            try:
                # The header text carries the "FCC-Extender" substring
                # in both the English and German catalogue entries, so
                # a substring match localises gracefully.
                marker = "FCC-Extender"
                for i, row in enumerate(self.list):
                    if not row:
                        continue
                    label = row[0]
                    if not isinstance(label, str) or marker not in label:
                        continue
                    self.list.insert(i + 1, (_detect_fccextender_status(),))
                    if hasattr(self, "config_list_widget"):
                        # Newer openatv builds use this attribute name
                        # for the widget that renders self.list.
                        self.config_list_widget.setList(self.list)
                    elif "config" in self and hasattr(self["config"], "setList"):
                        self["config"].setList(self.list)
                    break
            except Exception as exc:
                error("_inject_fccextender_status crashed (caught): %r" % exc)

        def _wire_fccextender_shortcut(self):
            """Register a yellow-button shortcut into the FCC-Extender
            settings screen, gated on the same opkg-status detection
            as the presence indicator. Stays out of the action map
            entirely when the companion plugin is absent so other
            yellow bindings from a host skin are untouched.
            """
            if not _fccextender_installed():
                return
            try:
                from Components.Sources.StaticText import StaticText
                from Components.ActionMap import HelpableActionMap
                label = _("FCC Extender")
                self["key_yellow"] = StaticText(label)
                self["entryActions"] = HelpableActionMap(
                    self,
                    ["ColorActions"],
                    {"yellow": (self._open_fccextender_setup, label)},
                    prio=0,
                    description=label,
                )
            except Exception as exc:
                error("_wire_fccextender_shortcut crashed (caught): %r"
                      % exc)

        def _open_fccextender_setup(self):
            """Yellow-button handler. The import is deferred to click
            time so a manually wiped plugin dir between the gate check
            and the keypress collapses to a logged no-op instead of a
            stack trace.
            """
            try:
                from Plugins.Extensions.FCCExtender.plugin import (
                    FCCExtenderSetup,
                )
                self.session.open(FCCExtenderSetup)
            except Exception as exc:
                error("opening FCCExtenderSetup failed (caught): %r"
                      % exc)

        def keySave(self):
            # Re-arm the live controller with the new toggles so the
            # user sees the change without restarting enigma2. Runs
            # *before* the parent's keySave because Setup.keySave
            # closes the screen on the happy path, and once closed
            # this instance is gone.
            #
            # The parent's keySave is the canonical save+close+restart-
            # prompt path: it calls saveAll() (which returns an empty
            # tuple on a normal save, or a (QUIT_RESTART, ...) /
            # (QUIT_REBOOT, ...) tuple when an item carries the
            # restart="gui" / restart="system" attribute). The
            # plugin's setup.xml uses neither marker, so saveAll() always
            # returns () here, and Setup.keySave() falls through to
            # self.close(). Delegating keeps that behaviour intact
            # while still firing the controller hook.
            try:
                from .controller import Controller
                ctrl = Controller.peek()
                if ctrl is not None:
                    ctrl.on_config_changed()
            except Exception as exc:
                error("on_config_changed crashed (caught): %r" % exc)
            Setup.keySave(self)

except Exception:
    # Off-box (tests) or enigma2 build without Screens.Setup: the
    # plugin keeps loading, but the settings screen is unreachable.
    # The plugin.open_setup wrapper already catches and logs that.
    pass


def open_setup(session, **kwargs):
    session.open(FBCChannelSpeedChangeSetup)
