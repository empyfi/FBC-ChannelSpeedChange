dnl FBC-ChannelSpeedChange - autoconf input
dnl
dnl Drives the OpenEmbedded build path. The Windows / quick-install
dnl path (build.py + Makefile in the repo root) is unaffected; this
dnl is purely for the OE recipe.

AC_INIT([enigma2-plugin-extensions-fbc-channelspeedchange],
        [0.3.3],
        [https://github.com/empyfi/FBC-ChannelSpeedChange/issues])

AC_PREREQ([2.69])
AC_CONFIG_AUX_DIR([build-aux])
AM_INIT_AUTOMAKE([foreign no-dist-gzip dist-xz subdir-objects])

AC_PROG_INSTALL
AC_PROG_MKDIR_P
AC_PATH_PROG([MSGFMT], [msgfmt], [no])
AS_IF([test "x$MSGFMT" = "xno"],
      [AC_MSG_ERROR([msgfmt not found - install gettext to build translations])])

AC_CONFIG_FILES([
    Makefile
    po/Makefile
])

AC_OUTPUT
