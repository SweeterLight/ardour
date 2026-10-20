#!/usr/bin/env bash
set -euo pipefail
PREFIX="$1"
WORK="$2"
JOBS="$3"
SOURCES="$WORK/sources"
NATIVE_PREFIX="$(cygpath -m "$PREFIX")"
NATIVE_RUNTIME="$(cygpath -m /clangarm64)"
mkdir -p "$SOURCES" "$WORK/dependency-builds" "$PREFIX/bin"

fetch() {
    local name="$1" upstream="$2"
    if [[ ! -d "$SOURCES/$name/.git" ]]; then
        git -c core.autocrlf=false clone --depth 1 "https://github.com/$upstream.git" "$SOURCES/$name"
    fi
    printf '%s https://github.com/%s %s\n' "$name" "$upstream" \
        "$(git -C "$SOURCES/$name" rev-parse HEAD)" >> "$WORK/source-revisions.txt"
}

: > "$WORK/source-revisions.txt"
fetch portaudio PortAudio/portaudio
fetch lrdf swh/LRDF
fetch harvid x42/harvid
fetch xjadeo x42/xjadeo

python - "$SOURCES/lrdf/lrdf_types.h" "$SOURCES/xjadeo/src/xjadeo/main.c" <<'PY'
from pathlib import Path
import sys
p = Path(sys.argv[1])
text = p.read_text()
if '#include <stdint.h>' not in text:
    p.write_text('#include <stdint.h>\n' + text)
p = Path(sys.argv[2])
text = p.read_text()
old = 'GetModuleFileName(NULL, (LPWCH)path, MAX_PATH);'
new = 'GetModuleFileNameA(NULL, path, sizeof(path));'
if old in text:
    p.write_text(text.replace(old, new))
elif new not in text:
    raise SystemExit('The xjadeo executable-path adapter needs review for this source revision')
PY

# Use a shared PortAudio with its complete public ASIO extension exports.
# PortAudio obtains the SDK directly from Steinberg. The SDK archive stays in
# the build directory; it is not included in the portable application.
cmake -S "$SOURCES/portaudio" -B "$WORK/dependency-builds/portaudio" -G Ninja \
    -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX="$NATIVE_PREFIX" \
    -DCMAKE_C_COMPILER=clang -DCMAKE_CXX_COMPILER=clang++ \
    -DPA_BUILD_SHARED_LIBS=ON -DPA_USE_ASIO=ON -DPA_USE_WASAPI=ON -DPA_USE_JACK=OFF \
    -DPA_BUILD_TESTS=OFF -DPA_BUILD_EXAMPLES=OFF
cmake --build "$WORK/dependency-builds/portaudio" --parallel "$JOBS"
cmake --install "$WORK/dependency-builds/portaudio"
sha256sum "$WORK/dependency-builds/portaudio/asiosdk.zip" > "$WORK/asiosdk.sha256"
python tools/windows-arm64/check_audio.py --stack "$NATIVE_PREFIX" \
    --runtime "$NATIVE_RUNTIME" --output "$(cygpath -m "$WORK/portaudio-capabilities.json")"

# LRDF is not available as an ARM64 package. A static archive avoids requiring
# a separate DLL export scheme for this small C dependency.
(
    cd "$SOURCES/lrdf"
    [[ -f configure ]] || autoreconf -fi
    if [[ ! -f "$PREFIX/lib/liblrdf.a" ]]; then
        CFLAGS='-O2 -std=gnu11' ./configure --host=aarch64-w64-mingw32 --prefix="$NATIVE_PREFIX" \
            --disable-shared --enable-static
    fi
    /usr/bin/make -C src -j"$JOBS"
    /usr/bin/make -C src install
    install -m644 lrdf.h lrdf_types.h "$PREFIX/include/"
    sed -i "s|^Libs:.*|Libs: -L\${libdir} -llrdf $(pkg-config --libs raptor2)|" lrdf.pc
    install -m644 lrdf.pc "$PREFIX/lib/pkgconfig/"
)

# Native MinGW Clang accepts the same Windows sources as the upstream cross
# builds. Command-line tool overrides take precedence over common.mak's GCC
# cross-tool names.
(
    cd "$SOURCES/harvid"
    /usr/bin/make -j"$JOBS" XWIN=aarch64-w64-mingw32 WINPREFIX="$NATIVE_RUNTIME" \
        CC=clang LD=ld.lld AR=llvm-ar RANLIB=llvm-ranlib NM=llvm-nm STRIP=llvm-strip
    install -m755 src/harvid.exe "$PREFIX/bin/harvid.exe"
)
(
    cd "$SOURCES/xjadeo"
    [[ -f configure ]] || autoreconf -fi
    CFLAGS='-O2 -std=gnu11' ./configure --host=aarch64-w64-mingw32 --target=aarch64-w64-mingw32 \
        --prefix="$NATIVE_PREFIX" --program-prefix= --with-fontfile=ArdourMono.ttf \
        --disable-xv --disable-imlib2 --disable-sdl --disable-mq --disable-ipc \
        --disable-portmidi --disable-alsamidi
    /usr/bin/make -C src/xjadeo -j"$JOBS"
    /usr/bin/make -C src/xjadeo install
    install -m755 src/xjadeo/xjadeo.exe "$PREFIX/bin/xjadeo.exe"
    install -m755 src/xjadeo/xjremote.exe "$PREFIX/bin/xjremote.exe"
)
