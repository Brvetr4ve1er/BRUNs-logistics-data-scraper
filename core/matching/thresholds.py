AUTO_MERGE_THRESHOLD = 0.85
REVIEW_THRESHOLD = 0.60

# Field weights for the weighted identity-similarity score. Kept here alongside
# the thresholds so all matching-policy constants live in one place. They must
# sum to 1.0 (asserted in tests).
WEIGHT_NAME = 0.6
WEIGHT_DOB = 0.3
WEIGHT_NATIONALITY = 0.1

# Score assigned to a corroborating field (DOB / nationality) when it is missing
# on either side. Deliberately BELOW 0.5: a missing field is not evidence of a
# match, so its absence should slightly lower confidence rather than sit neutral
# and inflate name-only matches into the auto-merge band.
MISSING_FIELD_SCORE = 0.3
