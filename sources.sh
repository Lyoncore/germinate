#!/bin/sh
# arch-tag: 2a2e98a5-eb42-4481-bbec-33502eaa3dcc

cat base desktop supported | sed -e '/^-/d;/^ /d;s/[^|]*| //;s/ *|.*//' | sort -u  | sed -e '1d'
