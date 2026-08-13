# Corresponding source and relink information

The release `NOTICE` and SPDX SBOM identify the exact packages and license
expressions. The following links provide the preferred source for every
reviewed copyleft component shipped by the locked production dependency set.
No package with an unreviewed copyleft license is permitted by the release
policy.

## sharp 0.35.3 / libvips 8.18.3

The platform-specific `@img/sharp-*` packages dynamically load the unmodified
libvips shared libraries distributed by the upstream sharp project. Source,
build scripts, and relinkable upstream releases are available at:

- https://github.com/lovell/sharp/tree/v0.35.3
- https://github.com/lovell/sharp-libvips/tree/v1.3.2
- https://github.com/libvips/libvips/tree/v8.18.3

The libvips portions are offered under LGPL-3.0-or-later. Recipients may replace
or relink those shared-library portions under the LGPL terms.

## psycopg / psycopg-binary 3.3.4

The locked Python distributions declare LGPL-3.0-only. Their corresponding
source and binary build metadata are available at:

- https://github.com/psycopg/psycopg/tree/3.3.4

## lightningcss 1.30.2

The locked Lightning CSS runtime and platform packages declare MPL-2.0. The
corresponding source is available at:

- https://github.com/parcel-bundler/lightningcss/tree/v1.30.2

## certifi 2026.2.25 / tqdm 4.67.3

These locked Python distributions include MPL-2.0 licensed material. Their
corresponding source and release metadata are available at:

- https://github.com/certifi/python-certifi/tree/2026.02.25
- https://github.com/tqdm/tqdm/tree/v4.67.3

If one of these upstream archives becomes unavailable, recipients may request
the corresponding source through the same release repository that supplied the
binary. This offer applies for as long as that binary release is distributed.
