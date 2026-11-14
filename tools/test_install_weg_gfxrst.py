#!/usr/bin/env python3
"""With WhateverGreen loaded and no gfxrst in boot-args, the installer adds gfxrst=4 itself.

WhateverGreen copies the boot screen on the first framebuffer start from a console mapping it saved at boot. macOS can
free that mapping before the NVIDIA display opens, and the copy then faults (panic under IOFramebuffer::postOpen).
gfxrst=4 turns the copy off. The installer used to print a note only. The rule is run here from the installer's own
function; the uninstaller's removal is run on its own lines against a stand-in nvram."""
import pathlib, subprocess, sys, tempfile, os

R = pathlib.Path(__file__).resolve().parents[1]
ins = (R / "package/install.sh").read_text()
unins = (R / "package/uninstall.sh").read_text()
fails = 0


def arm(name, ok, detail=""):
    global fails
    print(("ok   " if ok else "FAIL ") + name + (f" ({detail})" if detail and not ok else ""))
    fails += not ok


i = ins.index("weg_gfxrst() {"); fn = ins[i:ins.index("\n}\n", i) + 3]
i = ins.index("read_boot_args() {"); read_fn = ins[i:ins.index("\n}\n", i) + 3]
i = unins.index("read_boot_args() {")
arm("install and removal read NVRAM with the same fail-closed helper",
    read_fn == unins[i:unins.index("\n}\n", i) + 3])


def rule(args):
    return subprocess.run(["/bin/bash", "-u", "-c", fn + '\nweg_gfxrst "$1"', "x", args], capture_output=True, text=True).stdout.strip()


arm("no gfxrst: it is appended and the rest is kept", rule("nvfb=1 nvaccel=1") == "nvfb=1 nvaccel=1 gfxrst=4")
arm("empty boot-args: gfxrst=4 alone", rule("") == "gfxrst=4")
arm("gfxrst=4 already there: nothing to write", rule("nvfb=1 gfxrst=4") == "")
arm("a gfxrst the user chose stays as it is", rule("gfxrst=1 nvfb=1") == "")
arm("control: an argument that only contains the letters is not taken for one", rule("xgfxrst=4") == "xgfxrst=4 gfxrst=4")
blk = ins[ins.index("if kmutil showloaded --list-only --bundle-identifier as.vit9696.WhateverGreen"):]
blk = blk[:blk.index("\nfi\n") + 4]
arm("it is done only when WhateverGreen is loaded", 'weg_gfxrst "$ba"' in blk and 'weg_gfxrst_apply "$ba"' in blk)
apply_start = ins.index('weg_gfxrst_apply() {')
apply_fn = ins[apply_start:ins.index('\n}\n', apply_start)+3]
arm("the ownership record is published only after the NVRAM write worked",
    apply_fn.index('if ! nvram boot-args="$nb"') < apply_fn.index('mv -f "$temporary" "$record"'))
arm("the config.plist note is still printed", "OpenCore config.plist" in blk)

j = unins.index('if grep -qx "gfxrst=4" /Library/NullMoth/added-boot-args'); rm = unins[j:unins.index("\nfi\n", j) + 4]
with tempfile.TemporaryDirectory() as d:
    d = pathlib.Path(d); (d / "bin").mkdir()
    (d / "bin/nvram").write_text('''#!/bin/bash
S="%s/state"
case "$1" in
  -p)
    [ "${FAIL_READ:-0}" != 1 ] || exit 1
    [ "${FAIL_RECOVERY_READ:-0}" != 1 ] || [ ! -f "$S.written" ] || exit 1
    printf 'unrelated-variable\\tkeep-me\\n'
    [ ! -f "$S" ] || printf 'boot-args\\t%%s\\n' "$(cat "$S")"
    ;;
  boot-args=*)
    [ "${FAIL_NVRAM:-0}" != 1 ] || exit 1
    printf '%%s' "${1#boot-args=}" > "$S"
    touch "$S.written"
    ;;
  -d)
    [ "${FAIL_NVRAM:-0}" != 1 ] || exit 1
    rm -f "$S"
    ;;
  *) exit 1;;
esac
''' % d)
    os.chmod(d / "bin/nvram", 0o755)

    def removal(state, recorded):
        (d / "state").unlink(missing_ok=True)
        if state is not None:(d / "state").write_text(state)
        rec = d / "rec"
        if recorded: rec.write_text("gfxrst=4\n")
        elif rec.exists(): rec.unlink()
        subprocess.run(["/bin/bash", "-u", "-c", read_fn + rm.replace("/Library/NullMoth/added-boot-args", str(rec))],
                       env={"PATH": f"{d}/bin:/usr/bin:/bin"}, check=True)
        return (d / "state").read_text() if (d / "state").exists() else None, rec.exists()

    arm("uninstall takes out the gfxrst=4 it recorded and keeps the rest", removal("nvfb=1 gfxrst=4 nvaccel=1", True) == ("nvfb=1 nvaccel=1", False))
    arm("uninstall deletes boot-args when gfxrst=4 was all there was", removal("gfxrst=4", True) == (None, False))
    arm("control: with no record, a gfxrst=4 the user set is left alone", removal("nvfb=1 gfxrst=4", False) == ("nvfb=1 gfxrst=4", False))
    arm("removal preserves literal wildcard arguments", removal("debug=* gfxrst=4 nvfb=1", True) == ("debug=* nvfb=1", False))
    arm("removal accepts empty arguments under the installer's nounset shell", removal("", True) == ("", False))
    arm("removal accepts confirmed missing arguments without an NVRAM write", removal(None, True) == (None, False))
    # The failure cases run the production shell functions against stand-in native tools.
    (d/'bin/stat').write_text('#!/bin/bash\nprintf "0 755\\n"\n')
    (d/'bin/chown').write_text('#!/bin/bash\nexit 0\n')
    (d/'bin/mv').write_text('#!/bin/bash\n[ "${FAIL_RECORD:-0}" != 1 ] || exit 1\nexec /bin/mv "$@"\n')
    for name in ('stat','chown','mv'):os.chmod(d/'bin'/name,0o755)
    state_dir=d/'owned';state_dir.mkdir();record=state_dir/'added-boot-args'
    production=(read_fn+'\n'+fn+'\n'+apply_fn).replace('/Library/NullMoth',str(state_dir))+'\nweg_gfxrst_apply "$1"'
    def addition(value,**changes):
        if record.exists():record.unlink()
        (d/'state.written').unlink(missing_ok=True)
        (d/'state').unlink(missing_ok=True)
        if value is not None:(d/'state').write_text(value)
        env=dict(os.environ,PATH=f'{d}/bin:/usr/bin:/bin',**changes)
        result=subprocess.run(['/bin/bash','-u','-c',production,'fixture',value or ''],env=env,capture_output=True,text=True)
        return result.returncode,(d/'state').read_text() if (d/'state').exists() else None,record.exists()
    arm("successful addition records ownership and preserves existing arguments",
        addition('nvfb=1')==(0,'nvfb=1 gfxrst=4',True))
    arm("failed NVRAM write creates no ownership record",
        addition('nvfb=1',FAIL_NVRAM='1')==(1,'nvfb=1',False))
    arm("failed record publication restores the original NVRAM arguments",
        addition('nvfb=1',FAIL_RECORD='1')==(1,'nvfb=1',False))
    arm("a confirmed missing boot-args variable can receive the repair",
        addition(None)==(0,'gfxrst=4',True))
    arm("failed record publication restores a missing boot-args variable",
        addition(None,FAIL_RECORD='1')==(1,None,False))
    arm("a failed NVRAM read never overwrites existing boot-args",
        addition('nvfb=1',FAIL_READ='1')==(1,'nvfb=1',False))
    arm("a failed recovery read never deletes the newly written arguments",
        addition(None,FAIL_RECORD='1',FAIL_RECOVERY_READ='1')==(1,'gfxrst=4',False))
    arm("a user-selected gfxrst is retained without an ownership claim",
        addition('nvfb=1 gfxrst=1')==(0,'nvfb=1 gfxrst=1',False))
    # Failed removal must preserve the recovery record too.
    (d/'state').write_text('nvfb=1 gfxrst=4');rec=d/'rec';rec.write_text('gfxrst=4\n')
    subprocess.run(['/bin/bash','-u','-c',read_fn+rm.replace('/Library/NullMoth/added-boot-args',str(rec))],
                   env=dict(os.environ,PATH=f'{d}/bin:/usr/bin:/bin',FAIL_NVRAM='1'),check=True)
    arm("failed removal keeps boot-args and its recovery record",rec.exists() and (d/'state').read_text()=='nvfb=1 gfxrst=4')
    subprocess.run(['/bin/bash','-u','-c',read_fn+rm.replace('/Library/NullMoth/added-boot-args',str(rec))],
                   env=dict(os.environ,PATH=f'{d}/bin:/usr/bin:/bin',FAIL_READ='1'),check=True)
    arm("an unreadable NVRAM store keeps boot-args and its recovery record",rec.exists() and (d/'state').read_text()=='nvfb=1 gfxrst=4')
print("FAILED" if fails else "all ok")
sys.exit(1 if fails else 0)
