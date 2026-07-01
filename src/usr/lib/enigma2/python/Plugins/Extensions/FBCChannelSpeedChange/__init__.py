"""Package init.

Wires up gettext-based translation for user-facing strings. The `_`
symbol is exported and used throughout the package. When enigma2's
language infrastructure is unavailable (e.g. unit tests run off-box),
`_` falls back to an identity function so the package can still be
imported.

`__version__` is the canonical version string for the plugin package.
The Plugin Browser entry's description prefixes it so users (and the
OpenATV feed maintainer) can identify the build at a glance. It must
stay in sync with `CONTROL/control` and `Makefile` on every release;
`tools/bump_release_urls.py --check` enforces the three agree.
"""

import os


__version__ = "0.6.2"


def _(txt):
    """Identity fallback used when enigma2 i18n is not available."""
    return txt


try:
    import gettext

    from Components.Language import language
    from Tools.Directories import resolveFilename, SCOPE_PLUGINS

    _DOMAIN = "FBCChannelSpeedChange"
    _LOCALE_PATH = resolveFilename(
        SCOPE_PLUGINS, "Extensions/FBCChannelSpeedChange/locale"
    )

    def _localeInit():
        lang = language.getLanguage()
        os.environ["LANGUAGE"] = lang
        gettext.bindtextdomain(_DOMAIN, _LOCALE_PATH)
        gettext.textdomain(_DOMAIN)

    _localeInit()
    language.addCallback(_localeInit)

    def _(txt):
        t = gettext.dgettext(_DOMAIN, txt)
        if t == txt:
            return gettext.gettext(txt)
        return t

except Exception:
    # Off-box (tests) or enigma2 too old: keep the identity fallback.
    pass
