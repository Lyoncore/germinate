#!/usr/bin/env python
# -*- coding: UTF-8 -*-
# arch-tag:

import os,sys,getopt

class Package:
    def __init__(self,name):
        self.name=name
        self.seed={}
        self.installed=0
    def setSeed(self,seed):
        self.seed[seed]=1
    def setInstalled(self):
        self.installed=1
    def output(self,outmode):
        ret=self.name.ljust(30)+"\t"
        if outmode=="i":
            if self.installed and not len(self.seed):
                ret+="deinstall"
            elif not self.installed and len(self.seed):
                ret+="install"
            else:
                return ""
        elif outmode=="r":
            if self.installed and not len(self.seed):
                ret+="install"
            elif not self.installed and len(self.seed):
                ret+="deinstall"
            else:
                return ""
        else:           # default case
            if self.installed and not len(self.seed):
                ret="- "+ret
            elif not self.installed and len(self.seed):
                ret="+ "+ret
            else:
                ret="  "+ret
            k=self.seed.keys()
            k.sort()
            ret+=",".join(k)
        return ret


class Globals:
    def __init__(self):
        self.package={}
        self.outmode=""

    def parseSeed(self,seedname):
        """Parse an individual germinate output"""
        if not os.access(seedname,os.R_OK):
            print "Germinating"
            os.system("./germinate.py")
        f=open(seedname)
        lines=f.readlines()
        f.close()
        for l in lines[2:-2]:
            pkg=l.split(None)[0]
            self.package.setdefault(pkg,Package(pkg))
            self.package[pkg].setSeed(seedname)

    def parseDpkg(self,fname):
        if fname==None:
            f=os.popen("dpkg --get-selections")
        else:
            f=open(fname)
        lines=f.readlines()
        f.close()
        for l in lines:
            pkg,st=l.split(None)
            self.package.setdefault(pkg,Package(pkg))
            if st=="install" or st=="hold":
                self.package[pkg].setInstalled()

    def setOutput(self,mode):
        self.outmode=mode

    def output(self):
        keys=self.package.keys()
        keys.sort()
        for k in keys:
            l=self.package[k].output(self.outmode)
            if len(l):
                print l

def main():
    g=Globals()
    opts,args=getopt.getopt(sys.argv[1:],"l:m:")
    dpkgFile=None
    for o,v in opts:
        if o=='-l':
            dpkgFile=v
        elif o=="-m":
            # one of 'i' (install), 'r' (remove), or 'd' (default)
            g.setOutput(v)
    g.parseDpkg(dpkgFile)
    if not len(args):
        args=["base","desktop"]
    for fname in args:
        g.parseSeed(fname+".seed")
        g.parseSeed(fname+".depends")
    g.output()

if __name__ == "__main__":
    main()
