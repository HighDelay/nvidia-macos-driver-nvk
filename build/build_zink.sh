#!/bin/bash
# SPDX-License-Identifier: MIT
set -euo pipefail
if [ "$#" -ne 3 ]; then
  echo "usage: build_zink.sh MESA_SOURCE BUILD_DIRECTORY OUTPUT_DIRECTORY" >&2
  exit 2
fi
REPO=$(cd "$(dirname "$0")/.." && pwd)
MESA_SOURCE=$(cd "$1" && pwd)
mkdir -p "$2" "$3"
ZINK_BUILD=$(cd "$2" && pwd)
ZINK_OUTPUT=$(cd "$3" && pwd)
case "$ZINK_BUILD" in "$MESA_SOURCE"|"$MESA_SOURCE/build263")
  echo "Use a separate Zink build directory." >&2; exit 2;;
esac
source_args=()
if [ -n "${ZINK_MESA_ARCHIVE:-}" ]; then source_args=(--archive "$ZINK_MESA_ARCHIVE"); fi
python3 "$REPO/build/prepare_zink.py" "$MESA_SOURCE" ${source_args[@]+"${source_args[@]}"}
args=(-Dbuildtype=release -Dc_args=-mmacosx-version-min=15.5 -Dcpp_args=-mmacosx-version-min=15.5 -Dc_link_args=-mmacosx-version-min=15.5 -Dcpp_link_args=-mmacosx-version-min=15.5 -Dgallium-drivers=zink -Dvulkan-drivers= -Dplatforms=
      -Degl=disabled -Dglx=disabled -Dgbm=disabled -Dglvnd=disabled
      -Dllvm=disabled -Dshared-glapi=disabled -Dvulkan-layers= -Dtools=
      -Dbuild-tests=false -Dexpat=disabled -Dzstd=disabled -Dlibunwind=disabled
      -Dlmsensors=disabled -Dvalgrind=disabled -Dxlib-lease=disabled
      -Dgallium-rusticl=false -Dspirv-tools=disabled -Dmoltenvk-dir=)
if [ -f "$ZINK_BUILD/build.ninja" ]; then
  meson setup --reconfigure "$ZINK_BUILD" "$MESA_SOURCE" "${args[@]}"
else
  meson setup "$ZINK_BUILD" "$MESA_SOURCE" "${args[@]}"
fi
ninja -C "$ZINK_BUILD" -j "${ZINK_BUILD_JOBS:-4}" src/gallium/targets/nvk-zink/libnvk_zink.dylib src/gallium/targets/nvk-zink/zink_sync_probe
cp "$ZINK_BUILD/src/gallium/targets/nvk-zink/libnvk_zink.dylib" "$ZINK_OUTPUT/"
cp "$ZINK_BUILD/src/gallium/targets/nvk-zink/zink_sync_probe" "$ZINK_OUTPUT/"
cp "$REPO/zink/nvk_zink.h" "$ZINK_OUTPUT/"
cp "$MESA_SOURCE/src/gallium/targets/nvk-zink/cgl-api-requirements.json" "$ZINK_OUTPUT/"
install_name_tool -id @rpath/libnvk_zink.dylib "$ZINK_OUTPUT/libnvk_zink.dylib"
install_name_tool -add_rpath "${ZINK_VULKAN_RUNTIME:-/Library/GPUBundles/nvmtl/zink}" "$ZINK_OUTPUT/libnvk_zink.dylib"
codesign --force --sign - "$ZINK_OUTPUT/libnvk_zink.dylib"
clang -arch x86_64 -mmacosx-version-min=15.5 -std=c11 -Wall -Wextra -Werror \
  "$REPO/tools/zink_probe.c" -I"$REPO/zink" -framework IOSurface -framework CoreFoundation -o "$ZINK_OUTPUT/zink_probe"
clang -arch x86_64 -mmacosx-version-min=15.5 -std=c11 -Wall -Wextra -Werror -Wno-deprecated-declarations \
  "$REPO/tools/cgl_renderer_probe.c" -framework OpenGL -o "$ZINK_OUTPUT/cgl_renderer_probe"
for name in zink_probe zink_sync_probe cgl_renderer_probe; do codesign --force --sign - "$ZINK_OUTPUT/$name"; done
python3 "$REPO/build/record_zink_runtime.py" "$ZINK_OUTPUT" zink --mesa "$MESA_SOURCE/src/gallium/targets/nvk-zink/MESA-SOURCE.json"
echo "Zink runtime and probes: $ZINK_OUTPUT"
