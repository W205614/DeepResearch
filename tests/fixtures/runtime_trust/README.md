These are public CA certificates from Debian's historical `ca-certificates_20250419_all.deb`, used only to test rejection of the anchors removed by [DSA-6572-1](https://lists.debian.org/debian-security-announce/2026/msg00487.html). They are not application credentials, are never trusted by a runtime recipe and are not copied into runtime images.

Source: https://deb.debian.org/debian/pool/main/c/ca-certificates/ca-certificates_20250419_all.deb

The downloaded historical package SHA256 is `ef590f89563aa4b46c8260d49d1cea0fc1b181d19e8df3782694706adf05c184`. This download is a public negative-test input, not a signed-package installation claim. Actual replacement packages must pass authenticated APT and the separately pinned SHA256 gate.

| Public certificate | DER SHA256 |
| --- | --- |
| Comodo_AAA_Services_root.crt | d7a7a0fb5d7e2731d771e9484ebcdef71d5f0c3e0a2948782bc83ee0ea699ef4 |
| emSign_ECC_Root_CA_-_G3.crt | 86a1ecba089c4a8d3bbe2734c612ba341d813e043cf9e8a862cd5c57a36bbe6b |
