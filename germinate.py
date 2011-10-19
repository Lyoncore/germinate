#!/usr/bin/env python
# -*- coding: UTF-8 -*-
"""Update list files from the Wiki."""

# Copyright (c) 2004, 2005, 2006, 2007, 2008 Canonical Ltd.
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

import gzip
import os
import shutil
import sys
import urllib2
import getopt
import logging
import cStringIO

import apt_pkg

from Germinate import Germinator
import Germinate.Archive
import Germinate.seeds
import Germinate.version


__pychecker__ = 'maxlines=300'

# Where do we get up-to-date seeds from?
SEEDS = ["http://people.canonical.com/~ubuntu-archive/seeds/"]
SEEDS_BZR = ["http://bazaar.launchpad.net/~ubuntu-core-dev/ubuntu-seeds/"]
RELEASE = "ubuntu.oneiric"

# If we need to download Packages.gz and/or Sources.gz, where do we get
# them from?
MIRRORS = []
SOURCE_MIRRORS = []
DEFAULT_MIRROR = "http://archive.ubuntu.com/ubuntu/"
DEFAULT_SOURCE_MIRROR = None
DIST = ["oneiric"]
COMPONENTS = ["main", "restricted"]
ARCH = "i386"
INSTALLER_PACKAGES = True

# If we need to download a new IPv6 dump, where do we get it from?
IPV6DB = "http://debdev.fabbione.net/stat/"


def open_ipv6_tag_file(filename):
    """Download the daily IPv6 db dump if needed, and open it."""
    if os.path.exists(filename):
        return open(filename, "r")

    print "Downloading", filename, "file ..."
    url = IPV6DB + filename + ".gz"
    url_f = urllib2.urlopen(url)
    try:
        url_data = cStringIO.StringIO(url_f.read())
        try:
            print "Decompressing", filename, "file ..."
            gzip_f = gzip.GzipFile(fileobj=url_data)
            try:
                with open(filename, "w") as f:
                    for line in gzip_f:
                        print >>f, line,
            finally:
                gzip_f.close()
        finally:
            url_data.close()
    finally:
        url_f.close()

    return open(filename, "r")

def usage(f):
    print >>f, """Usage: germinate.py [options]

Options:

  -h, --help            Print this help message and exit.
  --version             Output version information and exit.
  -v, --verbose         Be more verbose when processing seeds.
  -S, --seed-source=SOURCE
                        Fetch seeds from SOURCE
                        (default: %s).
  -s, --seed-dist=DIST  Fetch seeds for distribution DIST (default: %s).
  -m, --mirror=MIRROR   Get package lists from MIRROR
                        (default: %s).
  --source-mirror=MIRROR
                        Get source package lists from mirror
                        (default: value of --mirror).
  -d, --dist=DIST       Operate on distribution DIST (default: %s).
  -a, --arch=ARCH       Operate on architecture ARCH (default: %s).
  -c, --components=COMPS
                        Operate on components COMPS (default: %s).
  -i, --ipv6            Check IPv6 status of source packages.
  --bzr                 Fetch seeds using bzr. Requires bzr to be installed.
  --cleanup             Don't cache Packages or Sources files.
  --no-rdepends         Disable reverse-dependency calculations.
  --no-installer        Do not consider debian-installer udeb packages.
  --seed-packages=PARENT/PKG,PARENT/PKG,...
                        Treat each PKG as a seed by itself, inheriting from
                        PARENT.
""" % (",".join(SEEDS), RELEASE, DEFAULT_MIRROR, ",".join(DIST), ARCH,
       ",".join(COMPONENTS))


def main():
    global SEEDS, SEEDS_BZR, RELEASE
    global DEFAULT_MIRROR, DEFAULT_SOURCE_MIRROR, SOURCE_MIRRORS, MIRRORS
    global DIST, ARCH, COMPONENTS, INSTALLER_PACKAGES
    verbose = False
    check_ipv6 = False
    bzr = False
    cleanup = False
    want_rdepends = True
    seed_packages = ()
    seeds_set = False

    g = Germinator()

    try:
        opts, args = getopt.getopt(sys.argv[1:], "hvS:s:m:d:c:a:i",
                                   ["help",
                                    "version",
                                    "verbose",
                                    "seed-source=",
                                    "seed-dist=",
                                    "mirror=",
                                    "source-mirror=",
                                    "dist=",
                                    "components=",
                                    "arch=",
                                    "ipv6",
                                    "bzr",
                                    "cleanup",
                                    "no-rdepends",
                                    "no-installer",
                                    "seed-packages="])
    except getopt.GetoptError:
        usage(sys.stderr)
        sys.exit(2)

    for option, value in opts:
        if option in ("-h", "--help"):
            usage(sys.stdout)
            sys.exit()
        elif option == "--version":
            print "%s %s" % (os.path.basename(sys.argv[0]),
                             Germinate.version.VERSION)
            sys.exit()
        elif option in ("-v", "--verbose"):
            verbose = True
        elif option in ("-S", "--seed-source"):
            SEEDS = value.split(",")
            seeds_set = True
        elif option in ("-s", "--seed-dist"):
            RELEASE = value
        elif option in ("-m", "--mirror"):
            if not value.endswith("/"):
                value += "/"
            MIRRORS.append(value)
        elif option == "--source-mirror":
            if not value.endswith("/"):
                value += "/"
            SOURCE_MIRRORS.append(value)
        elif option in ("-d", "--dist"):
            DIST = value.split(",")
        elif option in ("-c", "--components"):
            COMPONENTS = value.split(",")
        elif option in ("-a", "--arch"):
            ARCH = value
        elif option in ("-i", "--ipv6"):
            check_ipv6 = True
        elif option == "--bzr":
            bzr = True
            if not seeds_set:
                SEEDS = SEEDS_BZR
        elif option == "--cleanup":
            cleanup = True
        elif option == "--no-rdepends":
            want_rdepends = False
        elif option == "--no-installer":
            INSTALLER_PACKAGES = False
        elif option == "--seed-packages":
            seed_packages = value.split(',')

    if not MIRRORS:
        MIRRORS.append(DEFAULT_MIRROR)

    logger = logging.getLogger()
    if verbose:
        logger.setLevel(logging.DEBUG)
    else:
        logger.setLevel(Germinator.PROGRESS)
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter('%(levelname)s%(message)s'))
    logger.addHandler(handler)

    apt_pkg.init_config()
    apt_pkg.config.set("APT::Architecture", ARCH)
    apt_pkg.init_system()

    Germinate.Archive.TagFile(MIRRORS, SOURCE_MIRRORS, INSTALLER_PACKAGES).feed(
        g, DIST, COMPONENTS, ARCH, cleanup)

    if check_ipv6:
        with open_ipv6_tag_file("dailydump") as ipv6:
            g.parseIPv6(ipv6)

    if os.path.isfile("hints"):
        with open("hints") as hints:
            g.parseHints(hints)

    try:
        blacklist = Germinate.seeds.open_seed(SEEDS, RELEASE, "blacklist", bzr)
        try:
            g.parseBlacklist(blacklist)
        finally:
            blacklist.close()
    except Germinate.seeds.SeedError:
        pass

    try:
        seednames, seedinherit, seedbranches, _ = g.parseStructure(
            SEEDS, RELEASE, bzr)
    except Germinate.seeds.SeedError:
        sys.exit(1)

    g.writeStructureDot("structure.dot", seednames, seedinherit)

    seednames, seedinherit, seedbranches = g.expandInheritance(
        seednames, seedinherit, seedbranches)

    seedtexts = {}
    for seedname in seednames:
        try:
            seed_fd = Germinate.seeds.open_seed(SEEDS, seedbranches,
                                                seedname, bzr)
            try:
                seedtexts[seedname] = seed_fd.readlines()
            finally:
                seed_fd.close()
        except Germinate.seeds.SeedError:
            sys.exit(1)
        g.plantSeed(seedtexts[seedname],
                    ARCH, seedname, list(seedinherit[seedname]), RELEASE)
    for seed_package in seed_packages:
        (parent, pkg) = seed_package.split('/')
        g.plantSeed([" * " + pkg], ARCH, pkg,
                    seedinherit[parent] + [parent], RELEASE)
        seednames.append(pkg)
    g.prune()
    g.grow()
    g.addExtras(RELEASE)
    if want_rdepends:
        g.reverseDepends()

    seednames_extra = list(seednames)
    seednames_extra.append('extra')
    for seedname in seednames_extra:
        g.writeList(seedname, seedname,
                    set(g.seed[seedname]) | set(g.seedrecommends[seedname]) |
                    set(g.depends[seedname]))
        g.writeList(seedname, seedname + ".seed",
                    g.seed[seedname])
        g.writeList(seedname, seedname + ".seed-recommends",
                    g.seedrecommends[seedname])
        g.writeList(seedname, seedname + ".depends",
                    g.depends[seedname])
        g.writeList(seedname, seedname + ".build-depends",
                    g.build_depends[seedname])

        if seedname != "extra" and seedname in seedtexts:
            g.writeSeedText(seedname + ".seedtext", seedtexts[seedname])
            g.writeSourceList(seedname + ".sources",
                              g.sourcepkgs[seedname], check_ipv6)
        g.writeSourceList(seedname + ".build-sources",
                          g.build_sourcepkgs[seedname], check_ipv6)

    all_bins = set()
    sup_bins = set()
    all_srcs = set()
    sup_srcs = set()
    for seedname in seednames:
        all_bins.update(g.seed[seedname])
        all_bins.update(g.seedrecommends[seedname])
        all_bins.update(g.depends[seedname])
        all_bins.update(g.build_depends[seedname])
        all_srcs.update(g.sourcepkgs[seedname])
        all_srcs.update(g.build_sourcepkgs[seedname])

        if seedname == g.supported:
            sup_bins.update(g.seed[seedname])
            sup_bins.update(g.seedrecommends[seedname])
            sup_bins.update(g.depends[seedname])
            sup_srcs.update(g.sourcepkgs[seedname])

        # Only include those build-dependencies that aren't already in the
        # dependency outputs for inner seeds of supported. This allows
        # supported+build-depends to be usable as an "everything else"
        # output.
        build_depends = dict.fromkeys(g.build_depends[seedname], True)
        build_sourcepkgs = dict.fromkeys(g.build_sourcepkgs[seedname], True)
        for seed in g.innerSeeds(g.supported):
            build_depends.update(dict.fromkeys(g.seed[seed], False))
            build_depends.update(dict.fromkeys(g.seedrecommends[seed], False))
            build_depends.update(dict.fromkeys(g.depends[seed], False))
            build_sourcepkgs.update(dict.fromkeys(g.sourcepkgs[seed], False))
        sup_bins.update([k for (k, v) in build_depends.iteritems() if v])
        sup_srcs.update([k for (k, v) in build_sourcepkgs.iteritems() if v])

    g.writeList("all", "all", all_bins)
    g.writeSourceList("all.sources", all_srcs, check_ipv6)

    g.writeList("all", "%s+build-depends" % g.supported, sup_bins)
    g.writeSourceList("%s+build-depends.sources" % g.supported, sup_srcs,
                      check_ipv6)

    g.writeList("all", "all+extra", g.all)
    g.writeSourceList("all+extra.sources", g.all_srcs, check_ipv6)

    g.writeProvidesList("provides")

    g.writeStructure("structure")

    if os.path.exists("rdepends"):
        shutil.rmtree("rdepends")
    if want_rdepends:
        os.mkdir("rdepends")
        os.mkdir(os.path.join("rdepends", "ALL"))
        for pkg in g.all:
            dirname = os.path.join("rdepends", g.packages[pkg]["Source"])
            if not os.path.exists(dirname):
                os.mkdir(dirname)

            g.writeRdependList(os.path.join(dirname, pkg), pkg)
            os.symlink(os.path.join("..", g.packages[pkg]["Source"], pkg),
                       os.path.join("rdepends", "ALL", pkg))

    g.writeBlacklisted("blacklisted")

if __name__ == "__main__":
    main()
