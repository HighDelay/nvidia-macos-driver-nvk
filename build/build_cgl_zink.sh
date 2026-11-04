#!/bin/bash
# SPDX-License-Identifier: MIT
set -euo pipefail
if [ "$#" -ne 2 ]; then
  echo "usage: build_cgl_zink.sh ZINK_OUTPUT CGL_BUILD_DIRECTORY" >&2; exit 2
fi
REPO=$(cd "$(dirname "$0")/.." && pwd)
ZINK_OUTPUT=$(cd "$1" && pwd)
mkdir -p "$2"
CGL_BUILD=$(cd "$2" && pwd)
python3 "$REPO/build/generate_cgl_dispatch.py" "$ZINK_OUTPUT/cgl_renderer_probe" "$CGL_BUILD"
CGL_OS_BUILD=$(sw_vers -buildVersion)
clang -arch x86_64 -mmacosx-version-min=15.5 -fobjc-arc -fblocks -O2 -Wall -Wextra -Werror \
  -Wno-deprecated-declarations -fvisibility=hidden -c "$REPO/zink/appkit.m" \
  -o "$CGL_BUILD/appkit.o"
clang -arch x86_64 -mmacosx-version-min=15.5 -fobjc-arc -fblocks -O2 -Wall -Wextra -Werror \
  -Wno-deprecated-declarations -fvisibility=hidden -c "$REPO/zink/native_drawable.m" \
  -o "$CGL_BUILD/native_drawable.o"
clang -arch x86_64 -mmacosx-version-min=15.5 -std=c11 -O2 -Wall -Wextra -Werror -Wno-deprecated-declarations \
  -fvisibility=hidden \
  -I"$REPO/zink" -I"$CGL_BUILD" "$REPO/zink/nvk_cgl.c" "$CGL_BUILD/appkit.o" "$CGL_BUILD/native_drawable.o" \
  -dynamiclib -L"$ZINK_OUTPUT" -lnvk_zink -framework Cocoa -framework OpenGL -framework IOSurface -framework CoreFoundation -framework QuartzCore -framework Metal -framework IOKit \
  -Wl,-rpath,@loader_path -Wl,-rpath,/Library/GPUBundles/nvmtl \
  -install_name @rpath/libnvk_cgl.dylib -o "$ZINK_OUTPUT/libnvk_cgl.dylib"
codesign --force --sign - "$ZINK_OUTPUT/libnvk_cgl.dylib"
cp "$REPO/zink/nvk_cgl.h" "$ZINK_OUTPUT/"
printf '%s\n' "$CGL_OS_BUILD" > "$ZINK_OUTPUT/cgl-os-build.txt"
clang -arch x86_64 -mmacosx-version-min=15.5 -std=c11 -Wall -Wextra -Werror -Wno-deprecated-declarations \
  "$REPO/tools/cgl_zink_probe.c" -I"$REPO/zink" -framework OpenGL -o "$ZINK_OUTPUT/cgl_zink_probe"
clang -arch x86_64 -mmacosx-version-min=15.5 -std=c11 -Wall -Wextra -Werror -Wno-deprecated-declarations \
  "$REPO/tools/cgl_zink_legacy_probe.c" -I"$REPO/zink" -framework OpenGL -o "$ZINK_OUTPUT/cgl_zink_legacy_probe"
clang -arch x86_64 -mmacosx-version-min=15.5 -std=c11 -Wall -Wextra -Werror -Wno-deprecated-declarations \
  "$REPO/tools/cgl_iosurface_probe.c" -framework OpenGL -framework IOSurface -framework CoreVideo -framework CoreFoundation \
  -o "$ZINK_OUTPUT/cgl_iosurface_probe"
clang -arch x86_64 -mmacosx-version-min=15.5 -std=c11 -O2 -Wall -Wextra -Werror -Wno-deprecated-declarations \
  "$REPO/tools/cgl_iosurface_interop_probe.c" -framework OpenGL -framework IOSurface -framework CoreVideo -framework CoreFoundation \
  -o "$ZINK_OUTPUT/cgl_iosurface_interop_probe"
clang -arch x86_64 -mmacosx-version-min=15.5 -fobjc-arc -O2 -Wall -Wextra -Werror -Wno-deprecated-declarations \
  "$REPO/tools/obs_zink_policy_probe.m" -framework Cocoa -framework OpenGL -framework ScreenCaptureKit -framework CoreVideo \
  -o "$ZINK_OUTPUT/obs_zink_policy_probe"
clang -arch x86_64 -mmacosx-version-min=15.5 -fobjc-arc -Wall -Wextra -Werror -Wno-deprecated-declarations \
  "$REPO/tools/cgl_zink_window_probe.m" -I"$REPO/zink" -framework Cocoa -framework OpenGL -framework IOSurface \
  -o "$ZINK_OUTPUT/cgl_zink_window_probe"
clang -arch x86_64 -mmacosx-version-min=15.5 -fobjc-arc -O2 -Wall -Wextra -Werror -Wno-deprecated-declarations \
  "$REPO/tools/cgl_zink_front_probe.m" -I"$REPO/zink" -framework Cocoa -framework OpenGL \
  -o "$ZINK_OUTPUT/cgl_zink_front_probe"
clang++ -arch x86_64 -mmacosx-version-min=15.5 -std=c++17 -fobjc-arc -O2 \
  -Wall -Wextra -Werror -Wno-deprecated-declarations -Wno-missing-field-initializers \
  "$REPO/tools/zink_native_present_probe.mm" -I"$REPO/zink" -I"${VULKAN_HEADERS:-$HOME/Vulkan-Headers/include}" \
  -framework Cocoa -framework OpenGL -framework QuartzCore -o "$ZINK_OUTPUT/zink_native_present_probe"
cp "$CGL_BUILD/cgl-dispatch-map.json" "$ZINK_OUTPUT/"
for name in cgl_zink_probe cgl_zink_legacy_probe cgl_iosurface_probe cgl_iosurface_interop_probe obs_zink_policy_probe cgl_zink_window_probe cgl_zink_front_probe zink_native_present_probe; do codesign --force --sign - "$ZINK_OUTPUT/$name"; done
python3 "$REPO/build/record_zink_runtime.py" "$ZINK_OUTPUT" cgl
echo "Native CGL adapters generated on macOS $CGL_OS_BUILD: $ZINK_OUTPUT/libnvk_cgl.dylib"
