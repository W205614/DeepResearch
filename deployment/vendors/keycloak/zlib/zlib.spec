Name: zlib
Version: 1.4
Release: 1.deepresearch.el9
Summary: Official zlib 1.4 built locally against UBI9 glibc
License: Zlib
URL: https://zlib.net/
Source0: https://zlib.net/zlib-1.4.tar.gz
Vendor: DeepResearch local source build
BuildRequires: gcc, make
%global debug_package %{nil}
%global _build_id_links none

%description
Official upstream zlib 1.4, locally rebuilt on pinned UBI9. This package
replaces the actual zlib library and RPM inventory; it is not a Red Hat build.

%prep
%setup -q

%build
export SOURCE_DATE_EPOCH=1793750400
CC=gcc CFLAGS="-O2 -fPIC" ./configure --shared --prefix=/usr --libdir=/usr/lib64
make -j2

%install
mkdir -p %{buildroot}/usr/lib64 %{buildroot}/usr/share/licenses/zlib
install -m0755 libz.so.1.4 %{buildroot}/usr/lib64/libz.so.1.4
ln -s libz.so.1.4 %{buildroot}/usr/lib64/libz.so.1
install -m0644 README %{buildroot}/usr/share/licenses/zlib/README

%files
/usr/lib64/libz.so.1
/usr/lib64/libz.so.1.4
%license /usr/share/licenses/zlib/README
