# -*- coding: UTF-8 -*-
"""Fetch package lists from a Debian-format archive as apt tag files."""

# Copyright (c) 2004, 2005 Canonical Ltd.
#
# Germinate is free software; you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the
# Free Software Foundation; either version 2, or (at your option) any
# later version.
#
# Germinate is distributed in the hope that it will be useful, but
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
# General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with Germinate; see the file COPYING.  If not, write to the Free
# Software Foundation, 59 Temple Place - Suite 330, Boston, MA
# 02111-1307, USA.

import os
import urllib
import gzip

class TagFile:
    def __init__(self, mirror):
        self.mirror = mirror

    def open_tag_file(self, filename, dist, component, ftppath):
        """Download an apt tag file if needed, then open it."""
        if os.path.exists(filename):
            return open(filename, "r")

        print "Downloading", filename, "file ..."
        url = self.mirror + "dists/" + dist + "/" + component + "/" + ftppath
        gzip_fn = None
        try:
            gzip_fn = urllib.urlretrieve(url, filename + ".gz")[0]

            # apt_pkg is weird and won't accept GzipFile
            print "Decompressing", filename, "file ..."
            gzip_f = gzip.GzipFile(filename=gzip_fn)
            f = open(filename, "w")
            for line in gzip_f:
                print >>f, line,
            f.close()
            gzip_f.close()
        finally:
            if gzip_fn is not None:
                os.unlink(gzip_fn)

        return open(filename, "r")

    def feed(self, g, dists, components, arch):
        for dist in dists:
            for component in components:
                g.parsePackages(
                    self.open_tag_file(
                        "%s_%s_Packages" % (dist, component),
                        dist, component,
                        "binary-" + arch + "/Packages.gz"),
                    "deb")
                g.parseSources(
                    self.open_tag_file(
                        "%s_%s_Sources" % (dist, component),
                        dist, component,
                        "source/Sources.gz"))
                instpackages = ""
                try:
                    instpackages = self.open_tag_file(
                        "%s_%s_InstallerPackages" % (dist, component),
                        dist, component,
                        "debian-installer/binary-" + arch + "/Packages.gz")
                except IOError:
                    # can live without these
                    print "Missing installer Packages file for", component, \
                          "(ignoring)"
                else:
                    g.parsePackages(instpackages, "udeb")
