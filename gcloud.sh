#!/bin/sh
# Wrapper for the locally installed Cloud CLI, so nothing touches system PATH.
# Uses gcloud's own bundled Python: the system Python 3.14 shadows it with the
# Windows Store alias, and gcloud then fails with a misleading corruption error.
GC="/c/Users/biyon/gc/google-cloud-sdk"
CLOUDSDK_PYTHON="$(cygpath -w "$GC/platform/bundledpython/python.exe")" \
  exec "$GC/bin/gcloud.cmd" "$@"
