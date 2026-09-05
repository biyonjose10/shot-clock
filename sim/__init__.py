"""Offline simulation of a VFX render farm delivering a feature film.

The package is deliberately network-free: `film` is the static shot catalogue,
`farm` is the render-farm state machine, and `frames` writes PNG plates to disk.
Telemetry export and the diagnostic agents live outside this package and only
read from it.
"""
