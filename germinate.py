#!/usr/bin/env python
# -*- coding: UTF-8 -*-
# arch-tag: e7f7b26b-95bc-4432-86e6-832a7cc5ac01
"""Update list files from the Wiki.
"""

import apt_pkg
import gzip
import os
import sys
import urllib


# Where do we get up-to-date seeds from?
WIKI = "http://warthogs:wartyhoarygrumpy@www.warthogs.hbd.com/"
RELEASE = "WartyWarthog"

# If we need to download Packages.gz and/or Sources.gz, where do we get
# them from?
MIRROR = "http://ftp.debian.org/debian/"
DIST = "sid"
ARCH = "i386"


class Germinator:
    def __init__(self):
        self.packages = {}
        self.provides = {}
        self.sources = {}

        self.seeds = []
        self.seed = {}
        self.depends = {}
        self.build_depends = {}

        self.all = []
        self.nonb = []
        self.why = {}
        self.seeded = []

    def parsePackages(self, f):
        """Parse a Packages file and get the information we need."""
        p = apt_pkg.ParseTagFile(f)
        while p.Step() == 1:
            pkg = p.Section["Package"]
            self.packages[pkg] = {}

            self.packages[pkg]["Maintainer"] = p.Section.get("Maintainer", "")

            for field in "Depends", "Recommends", "Suggests":
                value = p.Section.get(field, "")
                self.packages[pkg][field] = apt_pkg.ParseDepends(value)


            for field in "Size", "Installed-Size":
                value = p.Section.get(field, "0")
                self.packages[pkg][field] = int(value)

            src = p.Section.get("Source", pkg)
            idx = src.find("(")
            if idx != -1:
                src = src[:idx].strip()
            self.packages[pkg]["Source"] = src

            provides = apt_pkg.ParseDepends(p.Section.get("Provides", ""))
            for prov in provides:
                if prov[0][0] not in self.provides:
                    self.provides[prov[0][0]] = []
                self.provides[prov[0][0]].append(pkg)
        f.close()

    def parseSources(self, f):
        """Parse a Sources file and get the information we need."""
        p = apt_pkg.ParseTagFile(f)
        while p.Step() == 1:
            src = p.Section["Package"]
            self.sources[src] = {}

            self.sources[src]["Maintainer"] = p.Section.get("Maintainer", "")

            for field in "Build-Depends", "Build-Depends-Indep":
                value = p.Section.get(field, "")
                self.sources[src][field] = apt_pkg.ParseSrcDepends(value)

            binaries = apt_pkg.ParseDepends(p.Section.get("Binary", src))
            self.sources[src]["Binaries"] = [ bin[0][0] for bin in binaries ]

        f.close()

    def plantSeed(self, seedname):
        """Add a seed."""
        if seedname in self.seeds:
            return

        self.seeds.append(seedname)
        self.seed[seedname] = []
        self.depends[seedname] = []
        self.build_depends[seedname] = []

        print "Downloading", seedname, "list ..."
        url = WIKI + RELEASE + seedname.title() + "Seed?action=raw"
        f = urllib.urlopen(url)
        for line in f:
            if not line.startswith(" * "):
                continue

            pkg = line[3:].strip()
            if pkg.find(" ") != -1:
                pkg = pkg[:pkg.find(" ")]

            if pkg in self.packages:
                if pkg not in self.seeded:
                    self.seed[seedname].append(pkg)
                    self.seeded.append(pkg)
                else:
                    print "! Duplicated seed:", pkg

            elif pkg in self.provides:
                # Virtual package, include everything
                print "* Virtual", seedname, "package:", pkg
                for vpkg in self.provides[pkg]:
                    if vpkg not in self.seeded:
                        print "  - " + vpkg
                        self.seed[seedname].append(vpkg)
                        self.seeded.append(vpkg)

            else:
                # No idea
                print "? Unknown", seedname, "package:", pkg
        f.close()

    def grow(self):
        """Grow the seeds."""
        for seedname in self.seeds:
            print "Resolving", seedname, "dependencies ..."
            for pkg in self.seed[seedname]:
                self.all.append(pkg)
                self.nonb.append(pkg)
                self.why[pkg] = seedname.title()
                self.addPackage(seedname, pkg)

    def alreadySatisfied(self, seedname, pkg, depend, build_depend=False):
        """Work out whether a dependency has already been satisfied."""
        if depend in self.packages:
            trylist = [ depend ]
        elif depend in self.provides:
            trylist = self.provides[depend]
        else:
            return False

        for trydep in trylist:
            if build_depend:
                if trydep in self.all:
                    return True
            else:
                if trydep in self.nonb:
                    return True
            if trydep in self.seed[seedname]:
                return True
        else:
            return False

    def addDependency(self, seedname, pkg, depend, build_depend=False):
        """Add a single dependency."""
        if depend in self.packages:
            virtual = None
            trylist = [ depend ]
        elif depend in self.provides:
            virtual = depend
            trylist = self.provides[depend]
        else:
            print "? Unknown dependency", depend, "by", pkg
            return

        # Last ditch effort to satisfy this by promoting lesser seeds to
        # higher dependencies
        found = False
        for trydep in trylist:
            seedidx = self.seeds.index(seedname) + 1
            for lesserseed in self.seeds[seedidx:len(self.seeds)]:
                if trydep in self.seed[lesserseed]:
                    if build_depend:
                        # I'll get you next time, Gadget!
                        return
                    self.seed[lesserseed].remove(trydep)
                    print "! Promoted", trydep, "from", lesserseed, "to", \
                          seedname, "to satisfy", pkg

                    depend = trydep
                    found = True
                    break
            if found: break

        if virtual is not None and not found:
            reallist = [ d for d in self.provides[virtual]
                         if d in self.packages ]
            if len(reallist):
                depend = reallist[0]
                print "* Chose", depend, "out of", virtual, "to satifsy", pkg
            else:
                print "? Nothing to choose out of", virtual, "to satisfy", pkg
                return

        if build_depend:
            self.build_depends[seedname].append(depend)
            self.why[depend] = self.packages[pkg]["Source"] + " (B)"
        else:
            if depend in self.all:
                # It was a build-depend, now it becomes a depend
                for buildseed in self.seeds:
                    if depend in self.build_depends[buildseed]:
                        self.build_depends[buildseed].remove(depend)
            self.depends[seedname].append(depend)
            self.why[depend] = pkg
            self.nonb.append(depend)
        self.all.append(depend)

        self.addPackage(seedname, depend, build_depend)

    def addDependencyTree(self, seedname, pkg, depends, build_depend=False):
        """Add a package's dependency tree."""
        for deplist in depends:
            for dep in deplist:
                if self.alreadySatisfied(seedname, pkg, dep[0], build_depend):
                    break
            else:
                if len(deplist) > 1:
                    reallist = [ d for d in deplist if d[0] in self.packages ]
                    if len(reallist):
                        depend = reallist[0][0]
                        print "* Chose", depend, "to satisfy", pkg
                    else:
                        print "? Nothing to choose to satisfy", pkg
                        continue
                else:
                    depend = deplist[0][0]
                self.addDependency(seedname, pkg, depend, build_depend)

    def addPackage(self, seedname, pkg, build_depend=False):
        """Add a package and its dependency trees."""
        self.addDependencyTree(seedname, pkg, self.packages[pkg]["Depends"],
                               build_depend)

        src = self.packages[pkg]["Source"]
        if src not in self.sources:
            print "? Missing source package:", src, "(for", pkg + ")"
            return

        self.addDependencyTree(seedname, pkg,
                               self.sources[src]["Build-Depends"], True);
        self.addDependencyTree(seedname, pkg,
                               self.sources[src]["Build-Depends-Indep"], True);


def open_tag_file(filename, ftppath):
    """Download an apt tag file if needed, then open it."""
    if os.path.exists(filename):
        return open(filename, "r")

    print "Downloading", filename, "file ..."
    url = MIRROR + "dists/" + DIST + "/main/" + ftppath
    gzip_fn = urllib.urlretrieve(url)[0]

    # apt_pkg is weird and won't accept GzipFile
    print "Decompressing", filename, "file ..."
    gzip_f = gzip.GzipFile(filename=gzip_fn)
    f = open(filename, "w")
    for line in gzip_f:
        print >>f, line,
    f.close()
    gzip_f.close()

    os.unlink(gzip_fn)
    return open(filename, "r")

def write_list(filename, g, list):
    pkg_len = len("Package")
    src_len = len("Source")
    why_len = len("Why")
    mnt_len = len("Maintainer")

    for pkg in list:
        _pkg_len = len(pkg)
        if _pkg_len > pkg_len: pkg_len = _pkg_len

        _src_len = len(g.packages[pkg]["Source"])
        if _src_len > src_len: src_len = _src_len

        _why_len = len(g.why[pkg])
        if _why_len > why_len: why_len = _why_len

        _mnt_len = len(g.packages[pkg]["Maintainer"])
        if _mnt_len > mnt_len: mnt_len = _mnt_len

    size = 0
    installed_size = 0

    list.sort()
    f = open(filename, "w")
    print >>f, "%-*s | %-*s | %-*s | %-*s | %-15s | %-15s" % \
          (pkg_len, "Package",
           src_len, "Source",
           why_len, "Why",
           mnt_len, "Maintainer",
           "Deb Size (B)",
           "Inst Size (KB)")
    print >>f, ("-" * pkg_len) + "-+-" + ("-" * src_len) + "-+-" \
          + ("-" * why_len) + "-+-" + ("-" * mnt_len) + "-+-" \
          + ("-" * 15) + "-+-" + ("-" * 15) + "-"
    for pkg in list:
        size += g.packages[pkg]["Size"]
        installed_size += g.packages[pkg]["Installed-Size"]
        print >>f, "%-*s | %-*s | %-*s | %-*s | %15d | %15d" % \
              (pkg_len, pkg,
               src_len, g.packages[pkg]["Source"],
               why_len, g.why[pkg],
               mnt_len, g.packages[pkg]["Maintainer"],
               g.packages[pkg]["Size"],
               g.packages[pkg]["Installed-Size"])
    print >>f, ("-" * (pkg_len + src_len + why_len + mnt_len + 9)) + "-+-" \
          + ("-" * 15) + "-+-" + ("-" * 15) + "-"
    print >>f, "%*s | %15d | %15d" % \
          ((pkg_len + src_len + why_len + mnt_len + 9), "",
           size, installed_size)

    f.close()

def main():
    g = Germinator()

    g.parsePackages(open_tag_file("Packages", "binary-"+ARCH+"/Packages.gz"))
    g.parseSources(open_tag_file("Sources", "source/Sources.gz"))

    for seedname in ("base", "desktop", "supported"):
        g.plantSeed(seedname)
    g.grow()

    for seedname in ("base", "desktop", "supported"):
        write_list(seedname + ".seed", g, g.seed[seedname])
        write_list(seedname + ".depends", g, g.depends[seedname])
        write_list(seedname + ".build-depends", g, g.build_depends[seedname])

    for seedname in ("base", "desktop"):
        write_list(seedname, g, g.seed[seedname] + g.depends[seedname])

    write_list("supported.only", g,
               g.seed["supported"] + g.depends["supported"])

    write_list("supported", g, g.seed["supported"] + g.depends["supported"]
               + g.build_depends["base"] + g.build_depends["desktop"]
               + g.build_depends["supported"])

if __name__ == "__main__":
    main()
