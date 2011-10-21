#!/usr/bin/env python
# -*- coding: UTF-8 -*-
"""Update list files from the Wiki."""

# Copyright (c) 2004, 2005, 2006, 2007, 2008, 2009, 2010, 2011
#               Canonical Ltd.
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
import shutil
import sys
import optparse
import logging

import apt_pkg

from Germinate import Germinator
import Germinate.Archive
import Germinate.defaults
import Germinate.seeds
import Germinate.version


def parse_options():
    parser = optparse.OptionParser(prog='germinate',
                                   version=Germinate.version.VERSION)
    parser.add_option('-v', '--verbose', dest='verbose', action='store_true',
                      default=False,
                      help='be more verbose when processing seeds')
    parser.add_option('-S', '--seed-source', dest='seeds', metavar='SOURCE',
                      help='fetch seeds from SOURCE (default: %s)' %
                           Germinate.defaults.seeds)
    parser.add_option('-s', '--seed-dist', dest='release', metavar='DIST',
                      default=Germinate.defaults.release,
                      help='fetch seeds for distribution DIST '
                           '(default: %default)')
    parser.add_option('-m', '--mirror', dest='mirrors', action='append',
                      metavar='MIRROR',
                      help='get package lists from MIRROR (default: %s)' %
                           Germinate.defaults.mirror)
    parser.add_option('--source-mirror', dest='source_mirrors',
                      action='append', metavar='MIRROR',
                      help='get source package lists from mirror '
                           '(default: value of --mirror)')
    parser.add_option('-d', '--dist', dest='dist',
                      default=Germinate.defaults.dist,
                      help='operate on distribution DIST (default: %default)')
    parser.add_option('-a', '--arch', dest='arch',
                      default=Germinate.defaults.arch,
                      help='operate on architecture ARCH (default: %default)')
    parser.add_option('-c', '--components', dest='components',
                      default='main,restricted', metavar='COMPS',
                      help='operate on components COMPS (default: %default)')
    parser.add_option('--bzr', dest='bzr', action='store_true', default=False,
                      help='fetch seeds using bzr (requires bzr to be '
                           'installed)')
    parser.add_option('--cleanup', dest='cleanup', action='store_true',
                      default=False,
                      help="don't cache Packages or Sources files")
    parser.add_option('--no-rdepends', dest='want_rdepends',
                      action='store_false', default=True,
                      help='disable reverse-dependency calculations')
    parser.add_option('--no-installer', dest='installer', action='store_false',
                      default=True,
                      help='do not consider debian-installer udeb packages')
    parser.add_option('--seed-packages', dest='seed_packages',
                      metavar='PARENT/PKG,PARENT/PKG,...',
                      help='treat each PKG as a seed by itself, inheriting '
                           'from PARENT')
    options, _ = parser.parse_args()

    if options.seeds is None:
        if options.bzr:
            options.seeds = Germinate.defaults.seeds_bzr
        else:
            options.seeds = Germinate.defaults.seeds
    options.seeds = options.seeds.split(',')

    if options.mirrors is None:
        options.mirrors = [Germinate.defaults.mirror]

    def canonicalise_mirror(mirror):
        if not mirror.endswith('/'):
            mirror += '/'
        return mirror

    options.mirrors = map(canonicalise_mirror, options.mirrors)
    if options.source_mirrors is not None:
        options.source_mirrors = map(canonicalise_mirror,
                                     options.source_mirrors)

    options.dist = options.dist.split(',')
    options.components = options.components.split(',')
    if options.seed_packages is None:
        options.seed_packages = []
    else:
        options.seed_packages = options.seed_packages.split(',')

    return options


def main():
    g = Germinator()

    options = parse_options()

    logger = logging.getLogger()
    if options.verbose:
        logger.setLevel(logging.DEBUG)
    else:
        logger.setLevel(Germinator.PROGRESS)
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter('%(levelname)s%(message)s'))
    logger.addHandler(handler)

    apt_pkg.init_config()
    apt_pkg.config.set("APT::Architecture", options.arch)
    apt_pkg.init_system()

    Germinate.Archive.TagFile(options.mirrors, options.source_mirrors,
                              options.installer).feed(
        g, options.dist, options.components, options.arch, options.cleanup)

    if os.path.isfile("hints"):
        with open("hints") as hints:
            g.parseHints(hints)

    try:
        blacklist = Germinate.seeds.open_seed(
            options.seeds, options.release, "blacklist", options.bzr)
        try:
            g.parseBlacklist(blacklist)
        finally:
            blacklist.close()
    except Germinate.seeds.SeedError:
        pass

    try:
        seednames, seedinherit, seedbranches, _ = g.parseStructure(
            options.seeds, options.release, options.bzr)
    except Germinate.seeds.SeedError:
        sys.exit(1)

    g.writeStructureDot("structure.dot", seednames, seedinherit)

    seednames, seedinherit, seedbranches = g.expandInheritance(
        seednames, seedinherit, seedbranches)

    seedtexts = {}
    for seedname in seednames:
        try:
            seed_fd = Germinate.seeds.open_seed(options.seeds, seedbranches,
                                                seedname, options.bzr)
            try:
                seedtexts[seedname] = seed_fd.readlines()
            finally:
                seed_fd.close()
        except Germinate.seeds.SeedError:
            sys.exit(1)
        g.plantSeed(seedtexts[seedname],
                    options.arch, seedname, list(seedinherit[seedname]),
                    options.release)
    for seed_package in options.seed_packages:
        (parent, pkg) = seed_package.split('/')
        g.plantSeed([" * " + pkg], options.arch, pkg,
                    seedinherit[parent] + [parent], options.release)
        seednames.append(pkg)
    g.prune()
    g.grow()
    g.addExtras(options.release)
    if options.want_rdepends:
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
                              g.sourcepkgs[seedname])
        g.writeSourceList(seedname + ".build-sources",
                          g.build_sourcepkgs[seedname])

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
    g.writeSourceList("all.sources", all_srcs)

    g.writeList("all", "%s+build-depends" % g.supported, sup_bins)
    g.writeSourceList("%s+build-depends.sources" % g.supported, sup_srcs)

    g.writeList("all", "all+extra", g.all)
    g.writeSourceList("all+extra.sources", g.all_srcs)

    g.writeProvidesList("provides")

    g.writeStructure("structure")

    if os.path.exists("rdepends"):
        shutil.rmtree("rdepends")
    if options.want_rdepends:
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
