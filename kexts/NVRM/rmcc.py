#!/usr/bin/env python3
# rmcc.py <src.c> <out.o> — compile an RM-internal C file with RM's OWN command line for osapi.c (compile_cmds.sh), only the
#   source, object and depfile swapped. The kext links it beside libnvkernel.a, so it must see RM's structs exactly as that did.
import os, shlex, subprocess, sys
src, out = os.path.abspath(sys.argv[1]), os.path.abspath(sys.argv[2])
rm = os.path.join(os.environ.get("OGKM") or os.path.expanduser("~/ogkm620"), "src/nvidia")   # build-nvrm.sh runs with HOME=<its home>; OGKM names the 620.70.06 RM tree
lines = [l for l in open(os.path.join(rm, "_out/Darwin_x86_64/compile_cmds.sh")) if "arch/nvalloc/unix/src/osapi.c" in l]
if len(lines) != 1: sys.exit(f"STOP: {len(lines)} osapi.c lines in compile_cmds.sh")
cmd = lines[0].split(" && ")[0]
toks = shlex.split(cmd)
n = toks.count("arch/nvalloc/unix/src/osapi.c")
if n != 1: sys.exit(f"STOP: osapi.c appears {n} times as a token")
toks[toks.index("arch/nvalloc/unix/src/osapi.c")] = src
for i, t in enumerate(toks):
    if t == "-o": toks[i + 1] = out
    if t in ("-MF", "-MT"): toks[i + 1] = out + ".d"
r = subprocess.run(toks, cwd=rm, capture_output=True, text=True)
sys.stderr.write(r.stderr[-6000:])
sys.exit(r.returncode)
