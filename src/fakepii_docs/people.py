"""Seeded generation of fictional people and labeled near misses."""

from __future__ import annotations

import random
from dataclasses import dataclass

from . import data


@dataclass(frozen=True)
class Person:
    entity: str
    first: str
    last: str
    email: str
    email_alt: str
    phone: str
    phone_alt: str
    ssn: str
    street: str
    city: str
    zip_code: str
    dob: str
    username: str

    @property
    def full(self) -> str:
        return f"{self.first} {self.last}"

    @property
    def address(self) -> str:
        return f"{self.street}, {self.city}, {data.STATE} {self.zip_code}"


@dataclass(frozen=True)
class NearMiss:
    """A string that looks like PII but is not, and must survive masking."""

    kind: str
    value: str
    context: str
    label: str


@dataclass(frozen=True)
class Plan:
    """Everything a generator needs: the people and the near misses."""

    seed: int
    fmt: str
    people: tuple[Person, ...]
    near_misses: tuple[NearMiss, ...]
    rng: random.Random

    def near(self, kind: str) -> NearMiss:
        return next(n for n in self.near_misses if n.kind == kind)


def _ssn(rng: random.Random) -> str:
    # 9xx and 000 area numbers are never issued as SSNs.
    area = rng.choice(["000", f"9{rng.randrange(100):02d}"])
    return f"{area}-{rng.randrange(1, 100):02d}-{rng.randrange(1, 10000):04d}"


def make_plan(seed: int, fmt: str, n_people: int = 3) -> Plan:
    """Build the reproducible cast for one document.

    The RNG is seeded with ``"<seed>:<fmt>"`` (string seeding is stable across
    runs and platforms), so each format gets different people for one seed.
    """
    if not 3 <= n_people <= 6:
        raise ValueError("n_people must be between 3 and 6")
    rng = random.Random(f"{seed}:{fmt}")
    firsts = rng.sample(data.FIRST_NAMES, n_people)
    lasts = rng.sample(data.LAST_NAMES, n_people)
    # Distinct 555-01xx line numbers: one per person plus one for the SKU.
    lines = rng.sample(range(100), n_people + 1)
    used_ssns: set[str] = set()
    people = []
    for i, (first, last) in enumerate(zip(firsts, lasts)):
        area = rng.choice(data.AREA_CODES)
        line = f"01{lines[i]:02d}"
        year = rng.randrange(1950, 2004)
        ssn = _ssn(rng)
        while ssn in used_ssns:
            ssn = _ssn(rng)
        used_ssns.add(ssn)
        people.append(Person(
            entity=f"person-{i + 1}",
            first=first,
            last=last,
            email=f"{first.lower()}.{last.lower()}@{data.EMAIL_DOMAINS[0]}",
            email_alt=f"{first[0].lower()}{last.lower()}@{data.EMAIL_DOMAINS[1]}",
            phone=f"({area}) 555-{line}",
            phone_alt=f"+1 {area} 555 {line}",
            ssn=ssn,
            street=f"{rng.randrange(1, 999)} {rng.choice(data.STREETS)}",
            city=rng.choice(data.CITIES),
            zip_code=f"000{rng.randrange(1, 100):02d}",
            dob=f"{year}-{rng.randrange(1, 13):02d}-{rng.randrange(1, 29):02d}",
            username=f"{first}{last}{year}",
        ))

    town = people[0].last
    sku = f"{rng.choice(data.AREA_CODES)}-555-01{lines[-1]:02d}"
    order = _ssn(rng)
    while order in used_ssns:
        order = _ssn(rng)
    near = (
        NearMiss(
            kind="place_named_like_surname",
            value=town,
            context=f"{town} Township",
            label=(f"'{town} Township' is a place name that shares its spelling with "
                   f"the surname of {people[0].full}; it is not personal data."),
        ),
        NearMiss(
            kind="product_code_phone_shape",
            value=sku,
            context=f"SKU {sku}",
            label=f"'{sku}' is a product code shaped like a phone number.",
        ),
        NearMiss(
            kind="reference_number_ssn_shape",
            value=order,
            context=f"Order #{order}",
            label=f"'{order}' is an order number shaped like an SSN.",
        ),
    )
    return Plan(seed=seed, fmt=fmt, people=tuple(people), near_misses=near, rng=rng)


def split_points(value: str, rng: random.Random, pieces: int = 3) -> list[str]:
    """Cut *value* into *pieces* non-empty chunks at seeded positions."""
    pieces = max(2, min(pieces, len(value)))
    cuts = sorted(rng.sample(range(1, len(value)), pieces - 1))
    bounds = [0, *cuts, len(value)]
    return [value[a:b] for a, b in zip(bounds, bounds[1:])]
