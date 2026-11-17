"""Import pinned DXVK/VKD3D fork packages without rebuilding their DLLs.

Each fork owns its sources, patch and compiler records. The driver checks those
records and imports a complete pair through the same interface for both formats.
"""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tarfile
import tempfile


PACKAGES = {
    "dxvk": {
        "version": "3.1.1+nvk-shared-fallback", "fork_version": "3.1.1-nvk.1",
        "prefix": "dxvk-nvk-3.1.1-1/",
        "archive_sha256": "b7cb40774f1209e8b014a2ae9c2faed374ff4ebeec2dbb7f49cf3589c6aed88e",
        "upstream_revision": "b1a1c99ab52b687cf950d62c88bc2fa316b41663",
        "source_revision": "e1c9938a60e1fbdb89a265e1087b2395ca17933e",
        "packaging_revision": "c8add916913a407b71482fe85e86bf8e68bf538f",
        "runtime_paths": ("src", "include", "subprojects", "meson.build", "meson_options.txt"),
        "dlls": ("d3d11.dll", "dxgi.dll"),
        "licenses": {"LICENSE": "LICENSES/DXVK-zlib.txt"},
        "record": "DXVK-BUILD.json",
    },
    "vkd3d": {
        "version": "3.1", "fork_version": "3.1-nvk.2",
        "prefix": "vkd3d-nvk-3.1-nvk.2/",
        "archive_sha256": "b2943e7391a0174ff68a73338577b8f943e2810fbb64fa74adf5bd16153b0f5d",
        "upstream_revision": "4522cc1a8b1347f1ab1b0f7b14a23ed9444d95d1",
        "source_revision": "495c43e17029bd12b70be0b76d09ef8529bf8264",
        "runtime_paths": ("libs", "include", "khronos", "subprojects", "meson.build", "meson_options.txt"),
        "dlls": ("d3d12.dll", "d3d12core.dll"),
        "licenses": {name: "LICENSES/vkd3d-proton-" + name + ".txt"
                     for name in ("LICENSE", "COPYING", "AUTHORS")},
        "record": "VKD3D-BUILD.json",
    },
}


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def git(source, *args, **kwargs):
    return subprocess.check_output(["git", *args], cwd=source, **kwargs)


def read_package(archive, spec):
    """Read regular files only, with complete internal checksum coverage."""
    if digest(archive) != spec["archive_sha256"]:
        raise ValueError("Unrecognized " + spec["fork_version"] + " archive")
    files = {}
    with tarfile.open(archive) as package:
        for member in package:
            if member.isdir():
                continue
            name = member.name.removeprefix(spec["prefix"])
            path = Path(name)
            if (not member.name.startswith(spec["prefix"]) or not name
                    or path.is_absolute() or ".." in path.parts or path.as_posix() != name
                    or any(part.startswith("._") or part == ".DS_Store" for part in path.parts)
                    or not member.isfile() or member.size > 64 * 1024 * 1024 or name in files):
                raise ValueError("Invalid component archive entry: " + member.name)
            with package.extractfile(member) as stream:
                files[name] = stream.read()
    sums = {}
    for line in files["SHA256SUMS"].decode().splitlines():
        value, name = line.split("  ", 1)
        if name in sums:
            raise ValueError("Duplicate component checksum: " + name)
        sums[name] = value
    if sums != {name: sha256(data) for name, data in files.items() if name != "SHA256SUMS"}:
        raise ValueError("Component archive checksums are incomplete or mismatched")
    return files


def dxvk_build(files, source, spec, revision):
    """Adapt the original DXVK release's split release/compiler records."""
    release = json.loads(files["RELEASE.json"])
    build = json.loads(files["DXVK-BUILD.json"])
    if (revision != spec["packaging_revision"] or release.get("documentation_commit") != revision
            or release.get("equivalent_code_commit") != spec["source_revision"]
            or release.get("build_source_base") != spec["upstream_revision"]
            or build.get("source_revision") != spec["upstream_revision"]
            or build.get("source_diff_sha256") != sha256(files["source-changes.patch"])
            or build.get("source_diff", "").encode() != files["source-changes.patch"]
            or build.get("source_files") != release.get("code_hashes_verified")):
        raise ValueError("DXVK source/build identity mismatch")
    changed = git(source, "diff", "--name-only", spec["upstream_revision"], spec["source_revision"],
                  "--", *spec["runtime_paths"], text=True).splitlines()
    if not changed or set(changed) != set(build["source_files"]):
        raise ValueError("DXVK source file coverage mismatch")
    for name, expected in build["source_files"].items():
        if digest(source / name) != expected:
            raise ValueError("DXVK runtime patch differs from source: " + name)
    for name in spec["dlls"]:
        original = "x64/" + name
        expected = sha256(files[original])
        if (release["dlls"].get(original) != expected
                or build["dlls"]["dxvk/" + original].get("packaged_sha256") != expected):
            raise ValueError("DXVK compiler output mismatch: " + original)
    return build


def vkd3d_build(files, source, spec, revision):
    metadata = json.loads(files["BUILD.json"])
    build = metadata["build"]
    if (metadata.get("version") != spec["fork_version"]
            or metadata.get("packaging_revision") != revision
            or metadata.get("runtime_source_revision") != spec["source_revision"]
            or metadata.get("upstream_revision") != spec["upstream_revision"]
            or metadata.get("runtime_modified") is not True
            or build.get("source_revision") != spec["source_revision"]
            or build.get("upstream_revision") != spec["upstream_revision"]
            or build.get("runtime_modified") is not True
            or build.get("runtime_patch_sha256") != sha256(files["source-changes.patch"])):
        raise ValueError("VKD3D source/build identity mismatch")
    for name in (*spec["licenses"], "source-changes.patch", *("x64/" + n for n in spec["dlls"])):
        if metadata["files"].get(name) != sha256(files[name]):
            raise ValueError("VKD3D artifact hash mismatch: " + name)
    for name in spec["dlls"]:
        original = "x64/" + name
        if build["files"].get(original) != sha256(files[original]):
            raise ValueError("VKD3D compiler output mismatch: " + original)
    return build


def verify_patch(files, source, spec, component):
    # Git's abbreviated object IDs and hunk context can differ across clones.
    # Apply the delivered patch privately and compare the actual runtime trees.
    changed = git(source, "diff", "--name-only", spec["upstream_revision"], spec["source_revision"],
                  "--", *spec["runtime_paths"], text=True).splitlines()
    with tempfile.TemporaryDirectory(prefix=component + "-patch-") as temporary:
        env = {**os.environ, "GIT_INDEX_FILE": str(Path(temporary) / "index")}
        git(source, "read-tree", spec["upstream_revision"], env=env)
        subprocess.run(["git", "apply", "--cached", "--whitespace=nowarn"], cwd=source,
                       env=env, input=files["source-changes.patch"], check=True)
        actual = git(source, "diff", "--cached", "--name-only", spec["upstream_revision"], env=env, text=True).splitlines()
        if not changed or set(actual) != set(changed) or git(
                source, "diff", "--cached", "--name-only", spec["source_revision"], "--", *spec["runtime_paths"], env=env):
            raise ValueError(component + " runtime patch differs from source")


def import_component(root, component, archive, source):
    """Verify the archive, source, patch, licenses and complete x64 DLL pair."""
    spec = PACKAGES[component]
    files = read_package(archive, spec)
    revision = git(source, "rev-parse", "HEAD", text=True).strip()
    if git(source, "status", "--porcelain", "--untracked-files=no", text=True).strip():
        raise ValueError("Use the clean matching " + component + " fork source")
    if git(source, "diff", "--name-only", spec["source_revision"], revision, "--", *spec["runtime_paths"]):
        raise ValueError(component + " runtime source changed since the recorded build")
    build = {"dxvk": dxvk_build, "vkd3d": vkd3d_build}[component](files, source, spec, revision)
    verify_patch(files, source, spec, component)
    for name in spec["licenses"]:
        if files[name] != (source / name).read_bytes():
            raise ValueError(component + " license/source mismatch: " + name)
    entries = {"x64/" + name: component + "/x64/" + name for name in spec["dlls"]}
    entries.update(spec["licenses"])
    entries["source-changes.patch"] = component + "/source-changes.patch"
    for name in spec["dlls"]:
        data = files["x64/" + name]
        offset = int.from_bytes(data[60:64], "little")
        if (len(data) < 64 or data[:2] != b"MZ" or data[offset:offset+6] != b"PE\0\0\x64\x86"
                or data[offset+24:offset+26] != b"\x0b\x02"):
            raise ValueError("Expected an x64 " + component + " DLL: " + name)
    # Validate everything before writing any artifacts to the caller's stage.
    hashes = {relative: sha256(files[name]) for name, relative in entries.items()}
    record = {key: spec[key] for key in ("version", "fork_version", "source_revision", "upstream_revision", "archive_sha256")}
    record.update(modified=True, packaging_revision=revision, files=hashes, build=build,
                  source_url="https://github.com/HighDelay/" + component + "-nvk/tree/" + spec["source_revision"])
    if component == "vkd3d":
        record["binding_policy"] = "Descriptor sets for NVK on Wine Darwin; other drivers/hosts unchanged"
    for name, relative in entries.items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(files[name])
        target.chmod(0o755 if name.endswith(".dll") else 0o644)
    (root / spec["record"]).write_text(json.dumps(record, indent=2) + "\n")
    identity = {key: value for key, value in record.items() if key not in ("files", "build", "source_url")}
    identity.update(build_record=spec["record"], patch=component + "/source-changes.patch")
    return identity, [*hashes, spec["record"]]
