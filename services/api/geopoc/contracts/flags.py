"""Controlled quality-flag codes for observations.

One registry holds every flag named in either version of the specification. Canonical codes are upper case
(V2 style); the lower-case V3 examples and V2 synonyms are accepted on input and normalised.
"""

# code -> (meaning, whether the flag on its own requires human review)
FLAGS: dict[str, tuple[str, bool]] = {
    # named in the specification
    "MISSING_CRS": ("The source declares no coordinate reference system.", True),
    "LOW_CONFIDENCE": ("The score is below the calibrated threshold configured for this model and class.", True),
    "BLUR": ("The source image is blurred enough to limit interpretation.", True),
    "LOW_RESOLUTION": ("The source resolution limits interpretation.", True),
    "METADATA_MISSING": ("Expected source metadata is absent.", False),
    "METADATA_INCOMPLETE": ("Source metadata could only partly be read.", False),
    "SOURCE_CONFLICT": ("Sources disagree about this finding.", True),
    "NOT_GEOREFERENCED": ("No geographic location is established for this finding.", False),
    "TIMESTAMP_UNKNOWN": ("The time of capture is not known.", False),
    # added by this project
    "INVALID_CRS": ("The source's CRS cannot be used.", True),
    "UNCALIBRATED_SCORE": (
        "No validated threshold exists for this model and class; the score is not a probability.",
        True,
    ),
    "UNCLASSIFIED": ("The class is outside the controlled taxonomy.", True),
    "EVIDENCE_INCOMPLETE": ("The evidence reference lacks the region or frame it should have.", True),
    "DUPLICATE_ASSET": ("The source is a byte-identical copy of another asset.", False),
    "OCCLUDED": ("The feature is partly hidden.", True),
    "PARTIAL_OUTPUT": ("The run that produced this was interrupted.", True),
    "DERIVED_ASSET": ("Computed by this application from other assets; it is not an original file.", False),
    "CRS_USER_ASSERTED": (
        "The CRS or geotransform used was recorded by a person, not read from the file; its correctness is not "
        "established by the software.",
        True,
    ),
}

# spellings found in the specification -> canonical code
ALIASES: dict[str, str] = {
    "UNKNOWN_CRS": "MISSING_CRS",  # V2 section 6 example list
}


def normalise_flag(value: str) -> str:
    """Return the canonical code, or raise ValueError for a flag outside the registry."""
    code = value.strip().upper()
    code = ALIASES.get(code, code)
    if code not in FLAGS:
        raise ValueError(f"unknown quality flag {value!r}")
    return code


def forces_review(code: str) -> bool:
    return FLAGS[code][1]
