#!/usr/bin/env python3
"""Exercise fork-package provenance and provider inheritance without a driver build."""
import copy
import io
import json
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest import mock

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "nvk/wine-macos"))
import component_packages as packages
import provider

REAL_RUN = subprocess.run


def fixture_platform(command, *args, **kwargs):
    """Keep real Git checks; skip only fixture helper/platform commands."""
    if command[0] in ("xcrun", "codesign") or Path(command[0]).name == "nvk-runtime":
        return subprocess.CompletedProcess(command, 0)
    return REAL_RUN(command, *args, **kwargs)


class PackageFixture:
    def __init__(self, root, component):
        self.component = component
        self.root = root / component
        self.source = self.root / "source"
        self.source.mkdir(parents=True)
        self.spec = copy.deepcopy(packages.PACKAGES[component])
        self.runtime_file = ("src/dxvk/dxvk_image.cpp" if component == "dxvk"
                             else "libs/vkd3d/device.c")
        path = self.source / self.runtime_file
        path.parent.mkdir(parents=True)
        path.write_text("int sharing = 1;\n")
        for name in self.spec["licenses"]:
            (self.source / name).write_text(component + " " + name + " license\n")
        self.git("init", "-q")
        self.spec["upstream_revision"] = self.commit("upstream")
        path.write_text("int sharing = 0;\n")
        self.spec["source_revision"] = self.commit("runtime repair")
        # Packaging/docs commits may follow the compiled runtime revision.
        (self.source / "README.md").write_text("setup\n")
        self.spec["packaging_revision"] = self.commit("packaging")
        patch = self.git("diff", "--binary", self.spec["upstream_revision"],
                         self.spec["source_revision"], "--", *self.spec["runtime_paths"])
        self.files = {name: (self.source / name).read_bytes() for name in self.spec["licenses"]}
        self.files["source-changes.patch"] = patch
        for index, name in enumerate(self.spec["dlls"]):
            data = bytearray(256)
            data[:2] = b"MZ"
            data[60:64] = (128).to_bytes(4, "little")
            data[128:134] = b"PE\0\0\x64\x86"
            data[152:154] = b"\x0b\x02"
            data[-1] = index
            self.files["x64/" + name] = bytes(data)
        if component == "dxvk":
            self.metadata = {
                "documentation_commit": self.spec["packaging_revision"],
                "equivalent_code_commit": self.spec["source_revision"],
                "build_source_base": self.spec["upstream_revision"],
                "code_hashes_verified": {self.runtime_file: packages.digest(path)},
            }
            self.build = {"source_revision": self.spec["upstream_revision"],
                          "source_diff": patch.decode(), "source_diff_sha256": packages.sha256(patch),
                          "source_files": self.metadata["code_hashes_verified"]}
        else:
            self.metadata = {"version": self.spec["fork_version"],
                             "packaging_revision": self.spec["packaging_revision"],
                             "runtime_source_revision": self.spec["source_revision"],
                             "upstream_revision": self.spec["upstream_revision"], "runtime_modified": True}
            self.build = {"source_revision": self.spec["source_revision"],
                          "upstream_revision": self.spec["upstream_revision"], "runtime_modified": True,
                          "runtime_patch_sha256": packages.sha256(patch)}
        self.sync_metadata()
        self.archive = self.root / "package.tar.gz"
        self.output = self.root / "provider"
        self.output.mkdir()
        self.write_archive()

    def git(self, *args):
        return subprocess.check_output(["git", *args], cwd=self.source, stderr=subprocess.PIPE)

    def commit(self, message):
        self.git("add", ".")
        self.git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                 "-c", "commit.gpgsign=false", "commit", "-qm", message)
        return self.git("rev-parse", "HEAD").decode().strip()

    def sync_metadata(self):
        dlls = {"x64/" + name: packages.sha256(self.files["x64/" + name]) for name in self.spec["dlls"]}
        if self.component == "dxvk":
            self.metadata["dlls"] = dlls
            self.build["dlls"] = {"dxvk/" + name: {"packaged_sha256": value} for name, value in dlls.items()}
            self.files["RELEASE.json"] = json.dumps(self.metadata).encode()
            self.files["DXVK-BUILD.json"] = json.dumps(self.build).encode()
        else:
            self.build["files"] = dlls
            self.metadata["files"] = {name: packages.sha256(data) for name, data in self.files.items()
                                      if name not in ("BUILD.json", "SHA256SUMS")}
            self.metadata["build"] = self.build
            self.files["BUILD.json"] = json.dumps(self.metadata).encode()

    def write_archive(self, checksums=True, extra=None):
        if checksums:
            self.files["SHA256SUMS"] = "".join(packages.sha256(data) + "  " + name + "\n"
                                               for name, data in sorted(self.files.items())
                                               if name != "SHA256SUMS").encode()
        with tarfile.open(self.archive, "w:gz") as archive:
            for name, data in self.files.items():
                member = tarfile.TarInfo(self.spec["prefix"] + name)
                member.size = len(data)
                archive.addfile(member, io.BytesIO(data))
            if extra:
                archive.addfile(extra, io.BytesIO(b"x") if extra.isfile() else None)
        self.spec["archive_sha256"] = packages.digest(self.archive)

    def load(self):
        with mock.patch.dict(packages.PACKAGES, {self.component: self.spec}):
            return packages.import_component(self.output, self.component, self.archive, self.source)


class ComponentPackageTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="provider-packages-")
        self.root = Path(directory.name)
        self.addCleanup(directory.cleanup)

    def fixtures(self):
        for component in ("dxvk", "vkd3d"):
            yield PackageFixture(self.root, component)

    def assert_rejected(self, fixture, reason):
        with self.assertRaisesRegex((ValueError, KeyError, subprocess.CalledProcessError), reason):
            fixture.load()
        self.assertEqual(list(fixture.output.iterdir()), [])

    def test_imports_complete_pairs_patches_licenses_and_provenance(self):
        for f in self.fixtures():
            identity, paths = f.load()
            self.assertTrue(identity["modified"])
            self.assertEqual(identity["source_revision"], f.spec["source_revision"])
            self.assertEqual(identity["archive_sha256"], packages.digest(f.archive))
            self.assertEqual((f.output / identity["patch"]).read_bytes(), f.files["source-changes.patch"])
            record = json.loads((f.output / identity["build_record"]).read_text())
            self.assertEqual(record["build"], f.build)
            for name, expected in record["files"].items():
                self.assertEqual(packages.digest(f.output / name), expected)
            self.assertEqual(set(paths), {*record["files"], identity["build_record"]})
            for name in f.spec["dlls"]:
                self.assertEqual((f.output / f.component / "x64" / name).read_bytes(), f.files["x64/" + name])
            for name, relative in f.spec["licenses"].items():
                self.assertEqual((f.output / relative).read_bytes(), (f.source / name).read_bytes())

    def test_unpinned_archive_is_rejected(self):
        for f in self.fixtures():
            f.spec["archive_sha256"] = "0" * 64
            self.assert_rejected(f, "Unrecognized")

    def test_internal_checksum_tampering_is_rejected(self):
        for f in self.fixtures():
            f.files["source-changes.patch"] += b"changed"
            f.write_archive(checksums=False)
            self.assert_rejected(f, "checksums")

    def test_missing_pair_member_is_rejected(self):
        for f in self.fixtures():
            del f.files["x64/" + f.spec["dlls"][1]]
            f.write_archive()
            self.assert_rejected(f, "x64/")

    def test_metadata_cannot_hide_changed_compiler_output(self):
        for f in self.fixtures():
            f.files["x64/" + f.spec["dlls"][1]] += b"different build"
            f.write_archive()
            self.assert_rejected(f, "(compiler output|artifact hash) mismatch")

    def test_matching_hashes_do_not_hide_wrong_architecture(self):
        for f in self.fixtures():
            name = "x64/" + f.spec["dlls"][1]
            f.files[name] = f.files[name].replace(b"PE\0\0\x64\x86", b"PE\0\0\x4c\x01")
            f.sync_metadata()
            f.write_archive()
            self.assert_rejected(f, "Expected an x64")

    def test_clean_source_must_match_runtime_revision(self):
        for f in self.fixtures():
            (f.source / f.runtime_file).write_text("another runtime\n")
            f.commit("unqualified runtime")
            self.assert_rejected(f, "runtime source changed")

    def test_dirty_checkout_is_rejected(self):
        for f in self.fixtures():
            (f.source / "README.md").write_text("dirty\n")
            self.assert_rejected(f, "clean matching")

    def test_packaging_revision_must_match(self):
        for f in self.fixtures():
            f.metadata["documentation_commit" if f.component == "dxvk" else "packaging_revision"] = "0" * 40
            f.sync_metadata()
            f.write_archive()
            self.assert_rejected(f, "source/build identity")

    def test_license_bytes_must_match_source(self):
        for f in self.fixtures():
            f.files["LICENSE"] = b"unrelated license"
            f.sync_metadata()
            f.write_archive()
            self.assert_rejected(f, "license/source mismatch")

    def test_runtime_patch_must_match_source(self):
        for f in self.fixtures():
            # Rewrite all patch hashes too; source verification must still fail.
            patch = f.files["source-changes.patch"].replace(b"sharing = 0", b"sharing = 2")
            f.files["source-changes.patch"] = patch
            if f.component == "dxvk":
                f.build.update(source_diff=patch.decode(), source_diff_sha256=packages.sha256(patch))
            else:
                f.build["runtime_patch_sha256"] = packages.sha256(patch)
            f.sync_metadata()
            f.write_archive()
            self.assert_rejected(f, "(runtime patch differs|source/build identity)")

    def test_patch_formatting_can_differ_when_runtime_bytes_match(self):
        for f in self.fixtures():
            patch = self.git_full_patch(f)
            self.assertNotEqual(patch, f.files["source-changes.patch"])
            f.files["source-changes.patch"] = patch
            if f.component == "dxvk":
                f.build.update(source_diff=patch.decode(), source_diff_sha256=packages.sha256(patch))
            else:
                f.build["runtime_patch_sha256"] = packages.sha256(patch)
            f.sync_metadata()
            f.write_archive()
            f.load()

    @staticmethod
    def git_full_patch(fixture):
        return fixture.git("diff", "--binary", "--full-index", fixture.spec["upstream_revision"],
                           fixture.spec["source_revision"], "--", *fixture.spec["runtime_paths"])

    def test_unsafe_and_duplicate_archive_entries_are_rejected(self):
        for f in self.fixtures():
            for name, kind in (("../escape", tarfile.REGTYPE),
                               ("x64/linked.dll", tarfile.SYMTYPE),
                               ("x64/" + f.spec["dlls"][0], tarfile.REGTYPE),
                               ("._metadata", tarfile.REGTYPE)):
                with self.subTest(entry=name):
                    member = tarfile.TarInfo(f.spec["prefix"] + name)
                    member.type = kind
                    member.size = 1 if kind == tarfile.REGTYPE else 0
                    member.linkname = "/outside" if kind == tarfile.SYMTYPE else ""
                    f.write_archive(extra=member)
                    self.assert_rejected(f, "Invalid component archive entry")


class ProviderInheritanceTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="provider-inheritance-")
        self.root = Path(directory.name)
        self.addCleanup(directory.cleanup)
        self.base = self.root / "base"
        self.base.mkdir()
        self.source = {"runtime_id": "fixture-provider", "platform": {"os": "macos", "arch": "x86_64", "minimum_macos": "15.5"},
                       "dxvk": {"version": "3.1.1+nvk-shared-fallback", "modified": True},
                       "vkd3d": {"version": "3.1", "fork_version": "3.1-nvk.2", "modified": True},
                       "presentation": {"implementation": "gpu-copy", "wine_child_options": 1},
                       "frontend_build": "FRONTEND-BUILD.json", "files": {}}
        paths = [*provider.CRITICAL, "dxvk/source-changes.patch", "DXVK-BUILD.json", "BUILD.json",
                 "FRONTEND-BUILD.json", "vkd3d/x64/d3d12.dll", "vkd3d/x64/d3d12core.dll",
                 "vkd3d/source-changes.patch", "VKD3D-BUILD.json"]
        paths.extend("LICENSES/" + name for name in ("frontend-PolyForm-Noncommercial-1.0.0.txt",
                     "DXVK-zlib.txt", "Mesa-license.rst", "Wine-LGPL-2.1.txt"))
        for relative in paths:
            path = self.base / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(("original " + relative).encode())
            self.source["files"][relative] = packages.digest(path)
        (self.base / "nvk-wine-runtime.json").write_text(json.dumps(self.source))
        self.output = self.root / "new-provider"

    def test_default_keeps_both_component_pairs_patches_and_records(self):
        # Simulate only platform validation/helper compilation. Component copying
        # and provider manifest/checksum construction run normally.
        with mock.patch.object(provider, "validate_wine_frontend"), \
                mock.patch.object(provider.subprocess, "run", side_effect=fixture_platform), \
                mock.patch.object(provider, "import_component") as importer:
            provider.build(self.base, self.output)
        importer.assert_not_called()
        result = json.loads((self.output / "nvk-wine-runtime.json").read_text())
        for component in ("dxvk", "vkd3d"):
            self.assertEqual(result[component], self.source[component])
            paths = [name for name in self.source["files"]
                     if name.startswith(component + "/") or name == component.upper() + "-BUILD.json"]
            for name in paths:
                self.assertEqual((self.output / name).read_bytes(), (self.base / name).read_bytes())
                self.assertEqual(result["files"][name], self.source["files"][name])
        for line in (self.output / "SHA256SUMS").read_text().splitlines():
            expected, name = line.split("  ", 1)
            self.assertEqual(packages.digest(self.output / name), expected)

    def test_archive_and_source_must_be_supplied_together(self):
        for component in ("dxvk", "vkd3d"):
            for suffix in ("archive", "source"):
                with self.subTest(component=component, suffix=suffix), \
                        self.assertRaisesRegex(ValueError, "Supply both --" + component):
                    provider.build(self.base, self.output, **{component + "_" + suffix: self.root / "input"})
                self.assertFalse(self.output.exists())

    def test_explicit_components_use_the_same_import_path_and_refresh_all_records(self):
        fixtures = [PackageFixture(self.root, name) for name in ("dxvk", "vkd3d")]
        inputs = {f.component + "_" + name: getattr(f, name) for f in fixtures for name in ("archive", "source")}
        with mock.patch.dict(packages.PACKAGES, {f.component: f.spec for f in fixtures}), \
                mock.patch.object(provider, "validate_wine_frontend"), \
                mock.patch.object(provider.subprocess, "run", side_effect=fixture_platform):
            provider.build(self.base, self.output, **inputs)
        result = json.loads((self.output / "nvk-wine-runtime.json").read_text())
        for f in fixtures:
            self.assertEqual(result[f.component]["source_revision"], f.spec["source_revision"])
            self.assertEqual(result[f.component]["archive_sha256"], packages.digest(f.archive))
            for original, relative in f.spec["licenses"].items():
                self.assertEqual((self.output / relative).read_bytes(), f.files[original])
            for name in f.spec["dlls"]:
                relative = f.component + "/x64/" + name
                self.assertEqual((self.output / relative).read_bytes(), f.files["x64/" + name])
                self.assertEqual(result["files"][relative], packages.sha256(f.files["x64/" + name]))
            patch = result[f.component]["patch"]
            record = result[f.component]["build_record"]
            self.assertEqual(result["files"][patch], packages.digest(self.output / patch))
            self.assertEqual(result["files"][record], packages.digest(self.output / record))
        for relative in provider.CRITICAL[:2]:
            self.assertEqual((self.output / relative).read_bytes(), (self.base / relative).read_bytes())

    def test_failed_component_import_does_not_leave_partial_provider(self):
        bad = self.root / "bad.tar.gz"
        bad.write_bytes(b"not a qualified package")
        for component in ("dxvk", "vkd3d"):
            with self.subTest(component=component), \
                    mock.patch.object(provider, "validate_wine_frontend"), \
                    mock.patch.object(provider.subprocess, "run", side_effect=fixture_platform), \
                    self.assertRaisesRegex(ValueError, "Unrecognized"):
                provider.build(self.base, self.output,
                               **{component + "_archive": bad, component + "_source": self.root})
            self.assertFalse(self.output.exists())
            self.assertFalse(list(self.root.glob(".wine-nvk-*")))


if __name__ == "__main__":
    unittest.main()
