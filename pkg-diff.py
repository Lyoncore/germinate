#!/usr/bin/env python
# -*- coding: UTF-8 -*-
# arch-tag:

# Copyright (c) 2004 Canonical Ltd.
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
# Software Foundation, 59 Temple Place - Suite 330, Boston, MA
# 02111-1307, USA.

import os, sys, getopt
import apt_pkg
from Germinate import Germinator
import Germinate.Archive

# TODO: cloned from germinate.py; should be common
SEEDS = "http://people.ubuntu.com/~cjwatson/seeds/"
RELEASE = "hoary"
MIRROR = "http://archive.ubuntu.com/ubuntu/"
DIST = ["hoary"]
COMPONENTS = ["main"]
ARCH = "i386"
SEEDINHERIT = {
    'desktop':          ['base'],
    'ship':             ['base', 'desktop'],
    'live':             ['base', 'desktop'],
    'supported':        ['base', 'desktop', 'ship', 'live'],
}

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

    def setSeeds(self, seeds):
        self.seeds = seeds

        global MIRROR, DIST, COMPONENTS, ARCH
        print "Germinating"
        g = Germinator()
        apt_pkg.InitConfig()
        apt_pkg.Config.Set("APT::Architecture", ARCH)

        Germinate.Archive.TagFile(MIRROR).feed(g, DIST, COMPONENTS, ARCH)

        for seedname in self.seeds:
            global SEEDS, RELEASE, SEEDINHERIT
            if seedname in SEEDINHERIT:
                seedinherit = SEEDINHERIT[seedname]
            else:
                seedinherit = []
            g.plantSeed(SEEDS, RELEASE, ARCH, seedname, seedinherit)
        g.prune()
        g.grow()

        for seedname in self.seeds:
            for pkg in g.seed[seedname]:
                self.package.setdefault(pkg, Package(pkg))
                self.package[pkg].setSeed(seedname + ".seed")
            for pkg in g.depends[seedname]:
                self.package.setdefault(pkg, Package(pkg))
                self.package[pkg].setSeed(seedname + ".depends")

    def parseDpkg(self, fname):
        if fname == None:
            f = os.popen("dpkg --get-selections")
        else:
            f = open(fname)
        lines = f.readlines()
        f.close()
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

def main():
    g = Globals()
    opts, args = getopt.getopt(sys.argv[1:], "l:m:")
    dpkgFile = None
    for o, v in opts:
        if o == '-l':
            dpkgFile = v
        elif o == "-m":
            # one of 'i' (install), 'r' (remove), or 'd' (default)
            g.setOutput(v)
    g.parseDpkg(dpkgFile)
    if not len(args):
        args = ["base", "desktop"]
    g.setSeeds(args)
    g.output()

if __name__ == "__main__":
    main()
