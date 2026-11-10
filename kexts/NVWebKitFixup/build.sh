#!/bin/bash
# build.sh <Lilu DEBUG kext> <MacKernelSDK checkout> <output directory>
set -euo pipefail
cd "$(dirname "$0")"
LILU_SDK=${1:?Lilu DEBUG kext}/Contents/Resources
KERNEL_SDK=${2:?MacKernelSDK checkout}
OUTPUT=${3:?output directory}
test -f "$LILU_SDK/Headers/plugin_start.hpp"
test -d "$KERNEL_SDK/Headers"
VERSION=$(/usr/libexec/PlistBuddy -c 'Print :CFBundleVersion' Info.plist)
mkdir -p "$OUTPUT/NVWebKitFixup.kext/Contents/MacOS"
FLAGS=(-arch x86_64 -mmacosx-version-min=15.0 -fapple-kext -mkernel -nostdinc
    -I"$KERNEL_SDK/Headers" -I"$LILU_SDK" -DKERNEL -DKERNEL_PRIVATE -DDRIVER_PRIVATE
    -DAPPLE -DNeXT -DPRODUCT_NAME=NVWebKitFixup -DMODULE_VERSION="$VERSION"
    -std=c++17 -fno-rtti -fno-exceptions -fno-builtin -fno-common -O2
    -Wall -Wextra -Werror -Wno-unused-parameter -Wno-overloaded-virtual)
xcrun clang++ "${FLAGS[@]}" -c nvwebkitfixup.cpp -o "$OUTPUT/nvwebkitfixup.o"
xcrun clang++ "${FLAGS[@]}" -Wno-sign-conversion -Wno-shadow -c "$LILU_SDK/Library/plugin_start.cpp" -o "$OUTPUT/plugin_start.o"
xcrun clang++ -arch x86_64 -mmacosx-version-min=15.0 -fapple-kext -nostdlib -Xlinker -kext \
    -L"$KERNEL_SDK/Library/x86_64" -lkmodc++ -lkmod -lcc_kext \
    "$OUTPUT/nvwebkitfixup.o" "$OUTPUT/plugin_start.o" -o "$OUTPUT/NVWebKitFixup.kext/Contents/MacOS/NVWebKitFixup"
cp Info.plist "$OUTPUT/NVWebKitFixup.kext/Contents/Info.plist"
plutil -lint "$OUTPUT/NVWebKitFixup.kext/Contents/Info.plist"
codesign --force --sign - "$OUTPUT/NVWebKitFixup.kext"
codesign --verify --strict "$OUTPUT/NVWebKitFixup.kext"
python3 ../../build/package_webkit.py --output "$OUTPUT" --lilu-sdk "${1}" --kernel-sdk "$KERNEL_SDK"
shasum -a 256 "$OUTPUT/NVWebKitFixup.kext/Contents/MacOS/NVWebKitFixup"
