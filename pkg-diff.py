#!/usr/bin/env python
# -*- coding: UTF-8 -*-

# Copyright (c) 2004, 2005, 2006, 2007, 2008, 2009, 2010, 2011
#               Canonical Ltd.
#
# This file is part of Germinate.
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

import sys
import optparse
import logging
import subprocess

import apt_pkg

from Germinate import Germinator
import Germinate.Archive
import Germinate.defaults
import Germinate.seeds
import Germinate.version

MIRRORS = [Germinate.defaults.mirror]
COMPONENTS = ["main"]

class Package:
    def __init__(self, name):
        self.name = name
        self.seed = {}
        self.installed = 0

    def setSeed(self, seed):
        self.seed[seed] = 1

    def setInstalled(self):
        self.installed = 1

    def output(self, outmode):
        ret = self.name.ljust(30) + "\t"
        if outmode == "i":
            if self.installed and not len(self.seed):
                ret += "deinstall"
            elif not self.installed and len(self.seed):
                ret += "install"
            else:
                return ""
        elif outmode == "r":
            if self.installed and not len(self.seed):
                ret += "install"
            elif not self.installed and len(self.seed):
                ret += "deinstall"
            else:
                return ""
        else:           # default case
            if self.installed and not len(self.seed):
                ret = "- " + ret
            elif not self.installed and len(self.seed):
                ret = "+ " + ret
            else:
                ret = "  " + ret
            k = self.seed.keys()
            k.sort()
            ret += ",".join(k)
        return ret


class Globals:
    def __init__(self):
        self.package = {}
        self.seeds = []
        self.outputs = {}
        self.outmode = ""

    def setSeeds(self, options, seeds):
        self.seeds = seeds

        # Suppress most log information
        logging.getLogger().setLevel(logging.CRITICAL)

        global MIRRORS, COMPONENTS
        print "Germinating"
        g = Germinator()
        apt_pkg.init_config()
        apt_pkg.config.set("APT::Architecture", options.arch)
        apt_pkg.init_system()

        archive = Germinate.Archive.TagFile(
            options.dist, COMPONENTS, options.arch, MIRRORS, cleanup=True)
        g.parseSections(archive)

        try:
            seednames, seedinherit, seedbranches, _ = g.parseStructure(
                options.seeds, options.release)
        except Germinate.seeds.SeedError:
            sys.exit(1)
        seednames, seedinherit, seedbranches = g.expandInheritance(
            seednames, seedinherit, seedbranches)
        needed_seeds = []
        build_tree = False
        for seedname in self.seeds:
            if seedname == ('%s+build-depends' % g.supported):
                seedname = g.supported
                build_tree = True
            for inherit in seedinherit[seedname]:
                if inherit not in needed_seeds:
                    needed_seeds.append(inherit)
            if seedname not in needed_seeds:
                needed_seeds.append(seedname)
        for seedname in needed_seeds:
            try:
                seed_fd = Germinate.seeds.open_seed(options.seeds,
                                                    seedbranches, seedname)
                try:
                    g.plantSeed(seed_fd,
                                options.arch, seedname,
                                list(seedinherit[seedname]), options.release)
                finally:
                    seed_fd.close()
            except Germinate.seeds.SeedError:
                sys.exit(1)
        g.prune()
        g.grow()

        for seedname in needed_seeds:
            for pkg in g.seed[seedname]:
                self.package.setdefault(pkg, Package(pkg))
                self.package[pkg].setSeed(seedname + ".seed")
            for pkg in g.seedrecommends[seedname]:
                self.package.setdefault(pkg, Package(pkg))
                self.package[pkg].setSeed(seedname + ".seed-recommends")
            for pkg in g.depends[seedname]:
                self.package.setdefault(pkg, Package(pkg))
                self.package[pkg].setSeed(seedname + ".depends")

            if build_tree:
                build_depends = dict.fromkeys(g.build_depends[seedname], True)
                for inner in g.innerSeeds(g.supported):
                    build_depends.update(dict.fromkeys(g.seed[inner], False))
                    build_depends.update(dict.fromkeys(g.seedrecommends[inner],
                                                       False))
                    build_depends.update(dict.fromkeys(g.depends[inner],
                                                       False))
                for (pkg, use) in build_depends.iteritems():
                    if use:
                        self.package.setdefault(pkg, Package(pkg))
                        self.package[pkg].setSeed(g.supported + ".build-depends")

    def parseDpkg(self, fname):
        if fname is None:
            dpkg_cmd = subprocess.Popen(['dpkg', '--get-selections'],
                                        stdout=subprocess.PIPE)
            try:
                lines = dpkg_cmd.stdout.readlines()
            finally:
                dpkg_cmd.wait()
        else:
            with open(fname) as f:
                lines = f.readlines()
        for l in lines:
            pkg, st = l.split(None)
            self.package.setdefault(pkg, Package(pkg))
            if st == "install" or st == "hold":
                self.package[pkg].setInstalled()

    def setOutput(self, mode):
        self.outmode = mode

    def output(self):
        keys = self.package.keys()
        keys.sort()
        for k in keys:
            l = self.package[k].output(self.outmode)
            if len(l):
                print l


def parse_options():
    epilog = '''\
A list of seeds against which to compare may be supplied as non-option
arguments.  Seeds from which they inherit will be added automatically.  The
default is 'desktop'.'''

    parser = optparse.OptionParser(prog='germinate-pkg-diff',
                                   usage='%prog [options] [seeds]',
                                   version=Germinate.version.VERSION,
                                   epilog=epilog)
    parser.add_option('-l', '--list', dest='dpkgFile', metavar='FILE',
                      help='read list of packages from this file '
                           '(default: read from dpkg --get-selections)')
    parser.add_option('-m', '--mode', dest='mode', type='choice',
                      choices=('i', 'r', 'd'), default='d', metavar='[i|r|d]',
                      help='show packages to install/remove/diff (default: d)')
    parser.add_option('-S', '--seed-source', dest='seeds', metavar='SOURCE',
                      default=Germinate.defaults.seeds,
                      help='fetch seeds from SOURCE (default: %s)' %
                           Germinate.defaults.seeds)
    parser.add_option('-s', '--seed-dist', dest='release', metavar='DIST',
                      default=Germinate.defaults.release,
                      help='fetch seeds for distribution DIST '
                           '(default: %default)')
    parser.add_option('-d', '--dist', dest='dist',
                      default=Germinate.defaults.dist,
                      help='operate on distribution DIST (default: %default)')
    parser.add_option('-a', '--arch', dest='arch',
                      default=Germinate.defaults.arch,
                      help='operate on architecture ARCH (default: %default)')

    options, args = parser.parse_args()

    options.seeds = options.seeds.split(',')
    options.dist = options.dist.split(',')

    return options, args


def main():
    g = Globals()

    options, args = parse_options()

    g.setOutput(options.mode)
    g.parseDpkg(options.dpkgFile)
    if not len(args):
        args = ["desktop"]
    g.setSeeds(options, args)
    g.output()

if __name__ == "__main__":
    main()
