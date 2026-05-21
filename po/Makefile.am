## po subdirectory automake input.
##
## Compiles every <lang>.po file in this directory into a binary
## .mo file at build time using the msgfmt detected by configure,
## then installs it under the plugin's locale tree at
## $(libdir)/enigma2/python/Plugins/Extensions/FBCChannelSpeedChange
## /locale/<lang>/LC_MESSAGES/FBCChannelSpeedChange.mo
##
## Adding a new translation: drop <lang>.po into this directory
## and append <lang> to LINGUAS below.

DOMAIN = FBCChannelSpeedChange

LINGUAS = de

POFILES = $(addsuffix .po,$(LINGUAS))
MOFILES = $(addsuffix .mo,$(LINGUAS))

plugin_localedir = \
    $(libdir)/enigma2/python/Plugins/Extensions/FBCChannelSpeedChange/locale

EXTRA_DIST = $(POFILES) $(DOMAIN).pot

all-local: $(MOFILES)

%.mo: %.po
	$(MSGFMT) -o $@ $<

install-data-local: $(MOFILES)
	@for lang in $(LINGUAS) ; do \
	    target="$(DESTDIR)$(plugin_localedir)/$$lang/LC_MESSAGES" ; \
	    $(MKDIR_P) "$$target" ; \
	    $(INSTALL_DATA) "$$lang.mo" "$$target/$(DOMAIN).mo" ; \
	done

uninstall-local:
	for lang in $(LINGUAS) ; do \
	    rm -f "$(DESTDIR)$(plugin_localedir)/$$lang/LC_MESSAGES/$(DOMAIN).mo" ; \
	done

clean-local:
	rm -f $(MOFILES)
