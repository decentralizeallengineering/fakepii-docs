"""Built-in fake data pools.

Every value here is chosen so that it cannot belong to a real person:

* surnames are obviously invented ("Example", "Samplewick" ...) and none
  occurs in the blank docx/pptx/xlsx templates (tested; "Placeholder" was
  dropped because the Office templates use that word);
* e-mail domains are ``example.invalid`` / ``example.com`` (RFC 2606 / RFC 6761);
* phone numbers use the NANP fictional block 555-0100..555-0199;
* SSN-shaped IDs start with 9xx or 000, which the SSA never issues;
* streets/cities are invented, the state code ``ZZ`` does not exist and ZIP
  codes are in the unassigned 000xx range.

The filler sentences are checked by the test-suite never to contain any of the
names below, so every occurrence of a name in a generated document is planted
on purpose and recorded in the ground truth.
"""

FIRST_NAMES = (
    "Ann", "Bramwell", "Cosima", "Dorrit", "Elowen", "Fenwick", "Gilda",
    "Hollis", "Isolde", "Jory", "Kestrel", "Linnet", "Marigold", "Nerys",
    "Orrin", "Pippa", "Rook", "Saffron", "Tamsin", "Ulla", "Vesper", "Wren",
    "Yarrow", "Zinnia",
)

LAST_NAMES = (
    "Example", "Samplewick", "Testerton", "Mockingbury",
    "Fictionson", "Dummyworth", "Nullington", "Pseudonymo", "Inventedale",
    "Madeupton", "Synthfield", "Bogusberg", "Exemplarby", "Fakenhurst",
    "Notrealis", "Unrealton",
)

STREETS = (
    "Make Believe Lane", "Nowhere Street", "Imaginary Avenue",
    "Hypothetical Road", "Fictional Way", "Pretend Court", "Null Boulevard",
    "Nonesuch Terrace",
)

CITIES = ("Fictionville", "Nulltown", "Mockport", "Samplestead", "Testhaven")

STATE = "ZZ"

AREA_CODES = ("213", "310", "415", "503", "612", "702", "808", "919")

EMAIL_DOMAINS = ("example.invalid", "example.com")

FILLER = (
    "Please review the schedule below before the Friday planning meeting.",
    "All figures are provisional until the quarterly reconciliation closes.",
    "Shift swaps must be logged in the shared tracker by the end of day.",
    "The loading dock will be closed for resurfacing next week.",
    "Contact the front office if any of the details below are out of date.",
    "Training hours are counted toward the certification requirement.",
    "Equipment returns go to the north storeroom, second shelf.",
    "This document is synthetic test data generated for masking evaluation.",
)
