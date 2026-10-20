#!/usr/bin/env bash
set -euo pipefail

if [[ -n "${ARDOUR_WINDOWS_REPO:-}" ]]; then
    cd "$(cygpath -u "$ARDOUR_WINDOWS_REPO")"
else
    cd "$(dirname "$0")/../.."
fi
REPO="$PWD"
WORK="$REPO/.build/windows-arm64"
PREFIX="$WORK/stack"
JOBS="${ARDOUR_BUILD_JOBS:-2}"
mkdir -p "$WORK/logs" "$PREFIX"
export PATH="$PREFIX/bin:/clangarm64/bin:/usr/bin:$PATH"
export CC=clang CXX=clang++ AR=llvm-ar RANLIB=llvm-ranlib
export PKG_CONFIG_PATH="$(cygpath -m "$PREFIX/lib/pkgconfig")"

if [[ "${MSYSTEM:-}" != CLANGARM64 ]]; then
    echo 'Run build.ps1 from Windows, or use an MSYS2 CLANGARM64 shell.' >&2
    exit 1
fi

case "${1:-All}" in
    Bootstrap)
        # A core runtime update can terminate the shell; perform it separately.
        pacman --noconfirm -Sy
        pacman --noconfirm -S --needed msys2-keyring
        pacman --noconfirm -Su
        ;;
    Dependencies)
        pacman --noconfirm -S --needed git make autoconf automake libtool patch \
            tar unzip zip diffutils gettext-devel vim \
            mingw-w64-clang-aarch64-{toolchain,python,cmake,ninja,pkgconf,boost,glib2,glibmm,libsndfile,curl,libarchive,liblo,taglib,vamp-plugin-sdk,rubberband,aubio,cairomm,pangomm,jack2,serd,sord,sratom,lilv,libwebsockets,libusb,fftw,libxml2,raptor,portaudio,libsamplerate,libpng,libjpeg-turbo,drmingw,cppunit,ffmpeg,SDL2}
        pacman -Q > "$WORK/packages.txt"
        ;&
    SourceDependencies)
        mkdir -p "$WORK/run"
        cp tools/windows-arm64/dependencies.sh "$WORK/run/dependencies-$$.sh"
        bash "$WORK/run/dependencies-$$.sh" "$PREFIX" "$WORK" "$JOBS"
        ;;
    Configure)
        [[ "$(clang -dumpmachine)" == aarch64-*-windows-* ]] || {
            echo 'Expected a native ARM64 Windows compiler.' >&2; exit 1;
        }
        # Waf validates environment overrides as filenames on Windows.
        export CC="$(cygpath -m "$(command -v clang)")"
        export CXX="$(cygpath -m "$(command -v clang++)")"
        export AR="$(cygpath -m "$(command -v llvm-ar)")"
        export AS="$CC"
        python waf configure --out="$(cygpath -m "$WORK/build")" \
            --prefix="$(cygpath -m "$WORK/stage")" \
            --libdir="$(cygpath -m "$WORK/stage/lib")" --configdir=share \
            --dist-target=mingw --windows-arch=arm64 \
            --check-c-compiler=clang --check-cxx-compiler=clang++ \
            --also-include="$(cygpath -m "$PREFIX/include")" \
            --also-libdir="$(cygpath -m "$PREFIX/lib")" \
            --with-backends=portaudio,jack,dummy --cxx17 --optimize --ptformat \
            --test --single-tests
        ;;
    Build)
        python waf -j"$JOBS" --targets=libpbd-tests
        python tools/windows-arm64/run_tests.py --pbd-only --work "$(cygpath -m "$WORK")" \
            --runtime "$(cygpath -m /clangarm64)"
        python waf -j"$JOBS"
        python tools/windows-arm64/compile_translations.py
        ;;
    Test)
        python tools/windows-arm64/compile_translations.py
        python waf install
        python tools/windows-arm64/run_tests.py --work "$(cygpath -m "$WORK")" \
            --runtime "$(cygpath -m /clangarm64)"
        ;;
    Package)
        python tools/windows-arm64/compile_translations.py
        python waf install
        python tools/windows-arm64/package.py --stage "$(cygpath -m "$WORK/stage")" \
            --stack "$(cygpath -m "$PREFIX")" --runtime "$(cygpath -m /clangarm64)" \
            --work "$(cygpath -m "$WORK")" --output "$(cygpath -m "$REPO/dist/Ardour-arm64")"
        ;;
    Validate)
        python tools/windows-arm64/validate_portable.py \
            --package "$(cygpath -m "$REPO/dist/Ardour-arm64")" --work "$(cygpath -m "$WORK")"
        python tools/windows-arm64/package.py --validate \
            --work "$(cygpath -m "$WORK")" \
            --output "$(cygpath -m "$REPO/dist/Ardour-arm64")"
        ;;
    *)
        echo "Build stage '$1' is not configured yet." >&2
        exit 1
        ;;
esac
