#!/usr/bin/env python
# -*- coding: UTF-8 -*-
# arch-tag: e7f7b26b-95bc-4432-86e6-832a7cc5ac01
"""Update list files from the Wiki.
"""

import apt_pkg
import gzip
import os
import shutil
import sys
import urllib


# Where do we get up-to-date seeds from?
WIKI = "http://warthogs:wartyhoarygrumpy@www.warthogs.hbd.com/"
RELEASE = "WartyWarthog"

# If we need to download Packages.gz and/or Sources.gz, where do we get
# them from?
MIRROR = "http://debdev.fabbione.net/debian/"
DIST = "sid"
ARCH = "i386"

# If we need to download a new IPv6 dump, where do we get it from?
IPV6DB= "http://debdev.fabbione.net/stat/"

class Germinator:
    def __init__(self):
        self.packages = {}
        self.provides = {}
        self.sources = {}

        self.seeds = []
        self.seed = {}
        self.depends = {}
        self.build_depends = {}

        self.sourcepkgs = {}
        self.build_sourcepkgs = {}

        self.pkgprovides = {}

        self.all = []
        self.not_build = []

        self.all_srcs = []
        self.not_build_srcs = []

        self.why = {}
        self.seeded = []

    def parsePackages(self, f):
        """Parse a Packages file and get the information we need."""
        p = apt_pkg.ParseTagFile(f)
        while p.Step() == 1:
            pkg = p.Section["Package"]
            self.packages[pkg] = {}

            self.packages[pkg]["Maintainer"] = p.Section.get("Maintainer", "")

            for field in "Pre-Depends", "Depends", "Recommends", "Suggests":
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
            self.packages[pkg]["Provides"] = provides
        f.close()

    def parseSources(self, f):
        """Parse a Sources file and get the information we need."""
        p = apt_pkg.ParseTagFile(f)
        while p.Step() == 1:
            src = p.Section["Package"]
            self.sources[src] = {}

            self.sources[src]["Maintainer"] = p.Section.get("Maintainer", "")
            self.sources[src]["IPv6"] = "Unknown"

            for field in "Build-Depends", "Build-Depends-Indep":
                value = p.Section.get(field, "")
                self.sources[src][field] = apt_pkg.ParseSrcDepends(value)

            binaries = apt_pkg.ParseDepends(p.Section.get("Binary", src))
            self.sources[src]["Binaries"] = [ bin[0][0] for bin in binaries ]

        f.close()

    def parseIPv6(self, f):
        """Parse the IPv6 dailydump file and get the information we need."""
        for line in f:
            (src, info) = line.split(None, 1)
            if src in self.sources:
                self.sources[src]["IPv6"] = info.strip()
        f.close()

    def newSeed(self, seedname):
        self.seeds.append(seedname)
        self.seed[seedname] = []
        self.depends[seedname] = []
        self.build_depends[seedname] = []
        self.sourcepkgs[seedname] = []
        self.build_sourcepkgs[seedname] = []

    def plantSeed(self, seedname):
        """Add a seed."""
        if seedname in self.seeds:
            return

        self.newSeed(seedname)

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
                self.addPackage(seedname, pkg, seedname.title() + " seed")

    def addExtras(self):
        """Add packages generated by the sources but not in any seed."""
        self.newSeed("extra")

        print "Identifying extras ..."
        for srcname in self.all_srcs:
            for pkg in self.sources[srcname]["Binaries"]:
                if pkg not in self.packages:
                    continue
                if pkg in self.all:
                    continue

                self.seed["extra"].append(pkg)
                self.addPackage("extra", pkg, "Generated by " + srcname,
                                second_class=True)

    def addReverse(self, pkg, field, rdep):
        """Add a reverse dependency entry."""
        if "Reverse-Depends" not in self.packages[pkg]:
            self.packages[pkg]["Reverse-Depends"] = {}
        if field not in self.packages[pkg]["Reverse-Depends"]:
            self.packages[pkg]["Reverse-Depends"][field] = []

        self.packages[pkg]["Reverse-Depends"][field].append(rdep)

    def reverseDepends(self):
        """Calculate the reverse dependency relationships."""
        for pkg in self.all:
            for field in "Pre-Depends", "Depends", "Recommends", "Suggests":
                for deplist in self.packages[pkg][field]:
                    for dep in deplist:
                        if dep[0] in self.all:
                            self.addReverse(dep[0], field, pkg)

        for src in self.all_srcs:
            for field in "Build-Depends", "Build-Depends-Indep":
                for deplist in self.sources[src][field]:
                    for dep in deplist:
                        if dep[0] in self.all:
                            self.addReverse(dep[0], field, src)

    def alreadySatisfied(self, seedname, pkg, depend, with_build=False):
        """Work out whether a dependency has already been satisfied."""
        if depend in self.packages:
            trylist = [ depend ]
        elif depend in self.provides:
            trylist = self.provides[depend]
        else:
            return False

        for trydep in trylist:
            if with_build:
                if trydep in self.all:
                    return True
            else:
                if trydep in self.not_build:
                    return True
            if trydep in self.seed[seedname]:
                return True
        else:
            return False

    def addDependency(self, seedname, pkg, depend, build_depend,
                      second_class, build_tree):
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
                    if second_class:
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

        if build_tree:
            self.build_depends[seedname].append(depend)
            if build_depend:
                why = self.packages[pkg]["Source"] + " (Build-Depend)"
            else:
                why = pkg
        else:
            self.depends[seedname].append(depend)
            why = pkg

        self.addPackage(seedname, depend, why, build_tree, second_class)

    def addDependencyTree(self, seedname, pkg, depends,
                          build_depend=False,
                          second_class=False,
                          build_tree=False):
        """Add a package's dependency tree."""
        if build_depend: build_tree = True
        if build_tree: second_class = True
        for deplist in depends:
            for dep in deplist:
                if self.alreadySatisfied(seedname, pkg, dep[0], second_class):
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
                self.addDependency(seedname, pkg, depend, build_depend,
                                   second_class, build_tree)

    def addPackage(self, seedname, pkg, why,
                   second_class=False,
                   build_tree=False):
        """Add a package and its dependency trees."""
        if build_tree: second_class=True
        if pkg not in self.all:
            self.all.append(pkg)
        elif not build_tree:
            for buildseed in self.seeds:
                if pkg in self.build_depends[buildseed]:
                    self.build_depends[buildseed].remove(pkg)
        if pkg not in self.not_build and not build_tree:
            self.not_build.append(pkg)

        self.why[pkg] = why

        for prov in self.packages[pkg]["Provides"]:
            if prov[0][0] not in self.pkgprovides:
                self.pkgprovides[prov[0][0]] = []
            if pkg not in self.pkgprovides[prov[0][0]]:
                self.pkgprovides[prov[0][0]].append(pkg)

        self.addDependencyTree(seedname, pkg,
                               self.packages[pkg]["Pre-Depends"],
                               second_class=second_class,
                               build_tree=build_tree)

        self.addDependencyTree(seedname, pkg, self.packages[pkg]["Depends"],
                               second_class=second_class,
                               build_tree=build_tree)

        src = self.packages[pkg]["Source"]
        if src not in self.sources:
            print "? Missing source package:", src, "(for", pkg + ")"
            return

        if second_class and src in self.all_srcs:
            return
        elif src in self.not_build_srcs:
            return

        if build_tree:
            self.all_srcs.append(src)
            self.build_sourcepkgs[seedname].append(src)
        else:
            if src in self.all_srcs:
                for buildseed in self.seeds:
                    if src in self.build_sourcepkgs[buildseed]:
                        self.build_sourcepkgs[buildseed].remove(src)
            else:
                self.all_srcs.append(src)

            self.not_build_srcs.append(src)
            self.sourcepkgs[seedname].append(src)

        self.addDependencyTree(seedname, pkg,
                               self.sources[src]["Build-Depends"],
                               build_depend=True)
        self.addDependencyTree(seedname, pkg,
                               self.sources[src]["Build-Depends-Indep"],
                               build_depend=True)


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

def open_ipv6_tag_file(filename):
    """Download the daily IPv6 db dump if needed, and open it."""
    if os.path.exists(filename):
        return open(filename, "r")

    print "Downloading", filename, "file ..."
    url = IPV6DB + filename + ".gz"
    gzip_fn = urllib.urlretrieve(url)[0]
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

def write_source_list(filename, g, list):
    src_len = len("Source")
    mnt_len = len("Maintainer")
    ipv6_len = len("IPv6 status")

    for src in list:
        _src_len = len(src)
        if _src_len > src_len: src_len = _src_len

        _mnt_len = len(g.sources[src]["Maintainer"])
        if _mnt_len > mnt_len: mnt_len = _mnt_len

	_ipv6_len = len(g.sources[src]["IPv6"])
	if _ipv6_len > ipv6_len: ipv6_len = _ipv6_len

    list.sort()
    f = open(filename, "w")
    print >>f, "%-*s | %-*s | %-*s" % (src_len, "Source",
                                       mnt_len, "Maintainer",
                                       ipv6_len, "IPv6 status")
    print >>f, ("-" * src_len) + "-+-" + ("-" * mnt_len) + "-+-" \
          + ("-" * ipv6_len) + "-"
    for src in list:
        print >>f, "%-*s | %-*s | %-*s" % (src_len, src, mnt_len,
                                           g.sources[src]["Maintainer"],
                                           ipv6_len,  g.sources[src]["IPv6"])
    f.close()

def write_rdepend_list(filename, g, pkg):
    f = open(filename, "w")
    _write_rdepend_list(f, g, pkg, "", done=[])
    f.close()

def _write_rdepend_list(f, g, pkg, prefix, stack=None, done=None):
    if stack is None:
        stack = []
    else:
        stack = list(stack)
        if pkg in stack:
            print >>f, prefix + pkg, "! loop !"
            return
    stack.append(pkg)

    if done is None:
        done = []
    elif pkg in done:
        print >>f, prefix + pkg, "! skipped !"
        return
    done.append(pkg)

    print >>f, prefix + pkg
    for seed in g.seeds:
        if pkg in g.seed[seed]:
            print >>f, prefix + "*", seed.title(), "seed"

    if "Reverse-Depends" not in g.packages[pkg]:
        return

    for field in ("Pre-Depends", "Depends", "Recommends", "Suggests",
                  "Build-Depends", "Build-Depends-Indep"):
        if field not in g.packages[pkg]["Reverse-Depends"]:
            continue

        deplist = g.packages[pkg]["Reverse-Depends"][field]
        deplist.sort()

        print >>f, prefix + "*", field + ":"
        for dep in deplist:
            print >>f, prefix + " +- " + dep
            if field.startswith("Build-") and dep not in g.all:
                continue

            if dep == deplist[-1]:
                extra = "    "
            else:
                extra = " |  "
            _write_rdepend_list(f, g, dep, prefix + extra, stack, done)

def write_prov_list(filename, g, dict):
    provides = dict.keys()
    provides.sort()

    f = open(filename, "w")
    for prov in provides:
        print >>f, prov

        list = dict[prov]
        list.sort()
        for pkg in list:
            print >>f, "\t%s" % (pkg,)
        print >>f
    f.close()

def main():
    g = Germinator()

    g.parsePackages(open_tag_file("Packages", "binary-"+ARCH+"/Packages.gz"))
    g.parseSources(open_tag_file("Sources", "source/Sources.gz"))
    g.parseIPv6(open_ipv6_tag_file("dailydump"))

    for seedname in ("base", "desktop", "supported"):
        g.plantSeed(seedname)
    g.grow()
    g.addExtras()
    g.reverseDepends()

    for seedname in ("base", "desktop", "supported", "extra"):
        write_list(seedname, g, g.seed[seedname] + g.depends[seedname])
        write_list(seedname + ".seed", g, g.seed[seedname])
        write_list(seedname + ".depends", g, g.depends[seedname])
        write_list(seedname + ".build-depends", g, g.build_depends[seedname])

        if seedname != "extra":
            write_source_list(seedname + ".sources",
                              g, g.sourcepkgs[seedname])
        write_source_list(seedname + ".build-sources",
                          g, g.build_sourcepkgs[seedname])

    all = []
    sup = []
    all_srcs = []
    sup_srcs = []
    for seedname in ("base", "desktop", "supported"):
        all += g.seed[seedname]
        all += g.depends[seedname]
        all += g.build_depends[seedname]
        all_srcs += g.sourcepkgs[seedname]
        all_srcs += g.build_sourcepkgs[seedname]

        if seedname == "supported":
            sup += g.seed[seedname]
            sup += g.depends[seedname]
            sup_srcs += g.sourcepkgs[seedname]
        sup += g.build_depends[seedname]
        sup_srcs += g.build_sourcepkgs[seedname]

    write_list("all", g, all)
    write_source_list("all.sources", g, all_srcs)

    write_list("supported+build-depends", g, sup)
    write_source_list("supported+build-depends.sources", g, sup_srcs)

    write_list("all+extra", g, g.all)
    write_source_list("all+extra.sources", g, g.all_srcs)

    write_prov_list("provides", g, g.pkgprovides)

    if os.path.exists("rdepends"):
        shutil.rmtree("rdepends")
    os.mkdir("rdepends")
    os.mkdir(os.path.join("rdepends", "ALL"))
    for pkg in g.all:
        dirname = os.path.join("rdepends", g.packages[pkg]["Source"])
        if not os.path.exists(dirname):
            os.mkdir(dirname)

        write_rdepend_list(os.path.join(dirname, pkg), g, pkg)
        os.symlink(os.path.join("..", g.packages[pkg]["Source"], pkg),
                   os.path.join("rdepends", "ALL", pkg))

if __name__ == "__main__":
    main()
