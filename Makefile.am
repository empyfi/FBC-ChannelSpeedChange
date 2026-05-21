## Top-level automake input - drives the OpenEmbedded build path.
## The Windows / quick-install path (build.py + plain Makefile) is
## untouched.

SUBDIRS = po

PLUGIN_PATH = src/usr/lib/enigma2/python/Plugins/Extensions/FBCChannelSpeedChange

pluginsdir = $(libdir)/enigma2/python/Plugins/Extensions/FBCChannelSpeedChange

PLUGIN_PY = \
    __init__.py \
    plugin.py \
    config.py \
    controller.py \
    diagnostic.py \
    fbc_pretune_pool.py \
    logger.py \
    osd_timing.py \
    predictor.py \
    resource_arbiter.py \
    settings_ui.py \
    zap_interceptor.py

PLUGIN_DATA = \
    plugin.png \
    setup_fbc_csc.xml

install-data-local:
	$(MKDIR_P) "$(DESTDIR)$(pluginsdir)"
	for f in $(PLUGIN_PY) $(PLUGIN_DATA) ; do \
	    $(INSTALL_DATA) "$(srcdir)/$(PLUGIN_PATH)/$$f" \
	        "$(DESTDIR)$(pluginsdir)/$$f" || exit 1 ; \
	done

uninstall-local:
	rm -rf "$(DESTDIR)$(pluginsdir)"

EXTRA_DIST = \
    autogen.sh \
    README.md \
    CHANGELOG.md \
    LICENSE \
    build.py \
    Makefile \
    CONTROL/control \
    tests/__init__.py \
    tests/_enigma_stubs.py \
    tests/run_all.py \
    tests/test_pool_state.py \
    tests/test_predictor.py \
    tests/test_resource_arbiter.py \
    tools/zap_stats.py \
    tools/compile_po.py \
    docs/install.md \
    docs/architecture.md

dist_pluginsrc_SOURCES =

# Include every plugin file in the source distribution so a `make
# dist` tarball is complete enough to rebuild the IPK.
EXTRA_DIST += \
    $(PLUGIN_PATH)/__init__.py \
    $(PLUGIN_PATH)/plugin.py \
    $(PLUGIN_PATH)/config.py \
    $(PLUGIN_PATH)/controller.py \
    $(PLUGIN_PATH)/diagnostic.py \
    $(PLUGIN_PATH)/fbc_pretune_pool.py \
    $(PLUGIN_PATH)/logger.py \
    $(PLUGIN_PATH)/osd_timing.py \
    $(PLUGIN_PATH)/predictor.py \
    $(PLUGIN_PATH)/resource_arbiter.py \
    $(PLUGIN_PATH)/settings_ui.py \
    $(PLUGIN_PATH)/zap_interceptor.py \
    $(PLUGIN_PATH)/plugin.png \
    $(PLUGIN_PATH)/setup_fbc_csc.xml
