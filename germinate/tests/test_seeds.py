#! /usr/bin/env python
"""Unit tests for germinate.seeds."""

# Copyright (C) 2012 Canonical Ltd.
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
try:
    import unittest2 as unittest
except ImportError:
    import unittest

from germinate.seeds import AtomicFile, Seed, SingleSeedStructure
from germinate.tests.helpers import TestCase


class TestAtomicFile(TestCase):
    def test_creates_file(self):
        """AtomicFile creates the named file with the requested contents."""
        self.useTempDir()
        with AtomicFile("foo") as test:
            test.write("string")
        with open("foo") as handle:
            self.assertEqual("string", handle.read())

    def test_removes_dot_new(self):
        """AtomicFile does not leave .new files lying around."""
        self.useTempDir()
        with AtomicFile("foo"):
            pass
        self.assertFalse(os.path.exists("foo.new"))


class TestSeed(TestCase):
    def setUp(self):
        self.addSeed("collection.dist", "test")
        self.addSeedPackage("collection.dist", "test", "foo")
        self.addSeed("collection.dist", "test2")
        self.addSeedPackage("collection.dist", "test2", "foo")
        self.addSeed("collection.dist", "test3")
        self.addSeedPackage("collection.dist", "test3", "bar")

    def test_init_no_bzr(self):
        """__init__ can open a seed from a collection without bzr."""
        seed = Seed(
            ["file://%s" % self.seeds_dir], ["collection.dist"], "test")
        self.assertEqual("test", seed.name)
        self.assertEqual("file://%s" % self.seeds_dir, seed.base)
        self.assertEqual("collection.dist", seed.branch)
        self.assertEqual(" * foo\n", seed.text)

    def test_behaves_as_file(self):
        """A Seed context can be read from as a file object."""
        seed = Seed(
            ["file://%s" % self.seeds_dir], ["collection.dist"], "test")
        with seed as seed_file:
            lines = list(seed_file)
            self.assertTrue(1, len(lines))
            self.assertTrue(" * foo\n", lines[0])

    def test_equal_if_same_contents(self):
        """Two Seed objects with the same text contents are equal."""
        one = Seed(
            ["file://%s" % self.seeds_dir], ["collection.dist"], "test")
        two = Seed(
            ["file://%s" % self.seeds_dir], ["collection.dist"], "test2")
        self.assertEqual(one, two)

    def test_not_equal_if_different_contents(self):
        """Two Seed objects with different text contents are not equal."""
        one = Seed(
            ["file://%s" % self.seeds_dir], ["collection.dist"], "test")
        three = Seed(
            ["file://%s" % self.seeds_dir], ["collection.dist"], "test3")
        self.assertNotEqual(one, three)


class TestSingleSeedStructure(TestCase):
    def test_basic(self):
        """A SingleSeedStructure object has the correct basic properties."""
        branch = "collection.dist"
        self.addSeed(branch, "base")
        self.addSeed(branch, "desktop", parents=["base"])
        seed = Seed(["file://%s" % self.seeds_dir], branch, "STRUCTURE")
        with seed as seed_file:
            structure = SingleSeedStructure(branch, seed_file)
        self.assertEqual(["base", "desktop"], structure.seed_order)
        self.assertEqual({"base": [], "desktop": ["base"]}, structure.inherit)
        self.assertEqual([branch], structure.branches)
        self.assertEqual(["base:", "desktop: base"], structure.lines)
        self.assertEqual(set(), structure.features)

    def test_include(self):
        """SingleSeedStructure parses the "include" directive correctly."""
        branch = "collection.dist"
        self.addStructureLine(branch, "include other.dist")
        seed = Seed(["file://%s" % self.seeds_dir], branch, "STRUCTURE")
        with seed as seed_file:
            structure = SingleSeedStructure(branch, seed_file)
        self.assertEqual([branch, "other.dist"], structure.branches)

    def test_feature(self):
        """SingleSeedStructure parses the "feature" directive correctly."""
        branch = "collection.dist"
        self.addStructureLine(branch, "feature follow-recommends")
        seed = Seed(["file://%s" % self.seeds_dir], branch, "STRUCTURE")
        with seed as seed_file:
            structure = SingleSeedStructure(branch, seed_file)
        self.assertEqual(set(["follow-recommends"]), structure.features)


if __name__ == "__main__":
    unittest.main()
