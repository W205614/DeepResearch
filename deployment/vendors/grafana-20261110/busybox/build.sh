#!/bin/sh
set -eu
export SOURCE_DATE_EPOCH=1768059508 TZ=UTC LC_ALL=C

cd /recipe
sha256sum -c files.sha256
mkdir -p /build-inputs /work/source-static /work/fixed-static /out/metadata /out/corresponding-source
while read -r digest filename url; do
    if [ -f "/recipe/inputs/$filename" ]; then
        cp "/recipe/inputs/$filename" "/build-inputs/$filename"
    else
        wget -q -O "/build-inputs/$filename" "$url"
    fi
    printf '%s  %s\n' "$digest" "/build-inputs/$filename" | sha256sum -c -
done < archives.lock

apk verify /build-inputs/*.apk > /out/metadata/apk-signatures.txt
apk add --no-network --no-cache /build-inputs/*.apk
apk info -vv > /out/metadata/build-packages.txt
sha256sum /etc/apk/keys/*.pub > /out/metadata/trusted-apk-keys.sha256
gcc --version > /out/metadata/compiler.txt
ld --version > /out/metadata/linker.txt

tar -xjf /build-inputs/busybox-1.37.0.tar.bz2 -C /work/source-static --strip-components=1
cd /work/source-static
while IFS= read -r name; do
    # Preserve Alpine's original patch application, including its existing test-context fuzz.
    patch -p1 < "/recipe/alpine-patches/$name"
done < /recipe/patch-order.txt
patch --fuzz=0 -p1 < /recipe/wget-http-request-boundary.patch
cp /recipe/busybox.config /work/fixed-static/.config
make O=/work/fixed-static silentoldconfig
extra_cflags=$(pkg-config --cflags --static libutmps)
# The approved runtime has only musl; do not introduce new shared-library dependencies.
extra_libs='-Wl,-Bstatic -lutmps -lskarnet -Wl,-Bdynamic'
printf '%s\n' "$extra_cflags" > /out/metadata/extra-cflags.txt
printf '%s\n' "$extra_libs" > /out/metadata/extra-libs.txt
make -j4 O=/work/fixed-static CONFIG_EXTRA_CFLAGS="$extra_cflags" CONFIG_EXTRA_LDLIBS="$extra_libs"
install -m755 /work/fixed-static/busybox /out/busybox
cp /work/fixed-static/.config /out/metadata/busybox.config
cp /recipe/source.lock.json /out/metadata/source.lock.json
cp /recipe/archives.lock /recipe/files.sha256 /out/metadata/
cp -R /recipe/licenses /out/
cp /recipe/build.sh /recipe/Dockerfile /recipe/patch-order.txt /recipe/busybox.config /recipe/wget-http-request-boundary.patch /recipe/archives.lock /recipe/files.sha256 /recipe/source.lock.json /recipe/write_metadata.py /recipe/regression.py /recipe/README.md /out/corresponding-source/
cp -R /recipe/alpine-patches /out/corresponding-source/
cp /recipe/upstream-APKBUILD /recipe/utmps-APKBUILD /recipe/skalibs-APKBUILD /out/corresponding-source/
cp /build-inputs/busybox-1.37.0.tar.bz2 /build-inputs/utmps-0.1.3.3.tar.gz /build-inputs/skalibs-2.15.0.0.tar.gz /out/corresponding-source/
readelf -d /out/busybox > /out/metadata/elf-dynamic.txt
readelf -n /out/busybox > /out/metadata/elf-notes.txt
/out/busybox --list > /out/metadata/applets.txt
python3 /recipe/write_metadata.py
