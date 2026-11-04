"""Provider-neutral satellite catalogue discovery (Phase 1): find scenes, download nothing.

A search sends a coarse bounding box of the area of interest, a date window and a cloud limit to a STAC catalogue. It is
off unless every gate is open and the person confirms each request (geopoc.imagery.service). Results are returned to the
caller and kept nowhere: no database row, no file, no cache. Credentials, if any, stay on the server.

Independent of geopoc.satellite and geopoc.earth_engine, which are left as they are.
"""
