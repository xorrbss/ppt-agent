# Sharp 0.35 adoption status

Status date: 2026-07-26

Sharp `0.35.3` is a real official release. The npm registry records version
`0.35.3` published at `2026-07-01T11:28:34.077Z`, with integrity
`sha512-ej0zVHuZGHCiABXcNxeYhpRnPNPAcvbG8RMdBAhDAxLKkCRVSpK3Iyu7qbqw3JMzoj0REeM6f3tJLtVwl0023Q==`.
The upstream project also publishes the
[`v0.35.3` GitHub release](https://github.com/lovell/sharp/releases/tag/v0.35.3).
The root application currently requests exactly `0.35.3`.

That does **not** resolve the export-runtime boundary. The pinned
`presentation-export v0.4.2` archive contains and loads Sharp `0.34.4`. Its
checksum-pinned binary runtime must remain internally consistent; replacing
only its JavaScript package from the root lockfile would create an unsupported
ABI mix.

Disposition: keep the v0.4.2 runtime isolated and checksum-verified. Adopt a
Sharp 0.35+ exporter only when `presenton/presenton-export` publishes an
official compatible release with per-platform assets. At that point, update the
version and every asset checksum in `compatibility/upstream-compatibility.json`,
sync on Linux/macOS/Windows, require runtime load checks, and pass the
cross-platform fidelity and package matrices. Until then this is a tracked
external blocker, not a dependency to fabricate.
