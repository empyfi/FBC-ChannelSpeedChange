#!/bin/sh
# Bootstrap the autotools build inside a fresh source checkout.
# After running this, ./configure && make && make install works.

set -e

cd "$(dirname "$0")"

mkdir -p build-aux

# aclocal pulls in macros from configure.ac; -I m4 would be needed if
# the project shipped local macros, which it does not at this point.
aclocal

automake --add-missing --copy --foreign

autoconf

echo "autogen.sh: done - now run ./configure && make"
