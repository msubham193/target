"""Google Earth Engine, GEE-1: organisation-owned authentication, a read-only dataset catalogue, and a manual connection
check, all inside operating model A (docs/earth-engine-integration-assessment.md, section 16.3a).

Operating model A means a controlled evaluation: named developers, one host, one process, fixed low server-side
ceilings, an append-only local usage log kept outside PostgreSQL, a global kill switch, and fail-closed behaviour
everywhere.

Nothing here imports the `ee` package at import time, sends an area of interest, serves a tile, exports or keeps
imagery, or touches the database. Importing this package contacts nobody.
"""
