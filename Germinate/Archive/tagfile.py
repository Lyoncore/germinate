# -*- coding: utf-8 -*-
"""Fetch package lists from a Debian-format archive as apt tag files."""

# Copyright (c) 2004, 2005, 2006, 2007 Canonical Ltd.
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
# Software Foundation, Inc., 51 Franklin St, Fifth Floor, Boston, MA
# 02110-1301, USA.

import os
import urllib2
import cStringIO
import gzip
import tempfile
import shutil

class TagFile:
    def __init__(self, mirrors, source_mirrors=None):
        self.mirrors = mirrors
        if source_mirrors is not None and len(source_mirrors) > 0:
            self.source_mirrors = source_mirrors
        else:
            self.source_mirrors = mirrors

    def open_tag_files(self, mirrors, dirname, tagfile_type,
                       dist, component, ftppath):
        def open_tag_file(mirror):
            """Download an apt tag file if needed, then open it."""
            url = mirror + "dists/" + dist + "/" + component + "/" + ftppath
            req = urllib2.Request(url)
            filename = None
            
            if req.get_type() != "file":
                filename = "%s_%s_%s_%s" % (req.get_host(), dist, component, tagfile_type)
            else:
                # Make a more or less dummy filename for local URLs.
                filename = os.path.split(req.get_selector())[0].replace(os.sep, "_")
            
            fullname = os.path.join(dirname, filename)
            if not os.path.exists(fullname):
                print "Downloading", req.get_full_url(), "file ..."

                url_f = urllib2.urlopen(req)
                url_data = cStringIO.StringIO(url_f.read())
                url_f.close()

                # apt_pkg is weird and won't accept GzipFile
                print "Decompressing", req.get_full_url(), "file ..."
                gzip_f = gzip.GzipFile(fileobj=url_data)
                f = open(fullname, "w")
                for line in gzip_f:
                    print >>f, line,

                f.close()
                gzip_f.close()
                url_data.close()

            return open(fullname, "r")
        
        return map(open_tag_file, mirrors)

    def feed(self, g, dists, components, arch, cleanup=False):
        if cleanup:
            dirname = tempfile.mkdtemp(prefix="germinate-")
        else:
            dirname = '.'

        for dist in dists:
            for component in components:
                g.parsePackages(
                    self.open_tag_files(
                        self.mirrors, dirname, "Packages", dist, component,
                        "binary-" + arch + "/Packages.gz"),
                    "deb")

                g.parseSources(
                    self.open_tag_files(
                        self.source_mirrors, dirname, "Sources", dist, component,
                        "source/Sources.gz"))

                instpackages = ""
                try:
                    instpackages = self.open_tag_files(
                        self.mirrors, dirname, "InstallerPackages", dist, component,
                        "debian-installer/binary-" + arch + "/Packages.gz")
                except IOError:
                    # can live without these
                    print "Missing installer Packages file for", component, \
                          "(ignoring)"
                else:
                    g.parsePackages(instpackages, "udeb")

        if cleanup:
            shutil.rmtree(dirname)
