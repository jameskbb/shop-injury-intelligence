"""The 25-column NEISS raw header contract, its snake_case mapping, and parse-time errors."""

from __future__ import annotations

# The exact 25-column header, in order, shared by every NEISS annual file (2006-2025).
RAW_COLUMNS: tuple[str, ...] = (
    "CPSC_Case_Number",
    "Treatment_Date",
    "Age",
    "Sex",
    "Race",
    "Other_Race",
    "Hispanic",
    "Body_Part",
    "Diagnosis",
    "Other_Diagnosis",
    "Body_Part_2",
    "Diagnosis_2",
    "Other_Diagnosis_2",
    "Disposition",
    "Location",
    "Fire_Involvement",
    "Product_1",
    "Product_2",
    "Product_3",
    "Alcohol",
    "Drug",
    "Narrative_1",
    "Stratum",
    "PSU",
    "Weight",
)

NCOLS = 25
assert len(RAW_COLUMNS) == NCOLS

# Race, Other_Race and Hispanic are dropped from every curated/interim output (D-09).
DROPPED_RAW_COLUMNS: tuple[str, ...] = ("Race", "Other_Race", "Hispanic")

# Raw header name -> snake_case output column name for every retained column.
RAW_TO_SNAKE: dict[str, str] = {
    "CPSC_Case_Number": "case_id",
    "Treatment_Date": "treatment_date",
    "Age": "age_raw",
    "Sex": "sex_code",
    "Body_Part": "body_part_code",
    "Diagnosis": "diagnosis_code",
    "Other_Diagnosis": "other_diagnosis",
    "Body_Part_2": "body_part_2_code",
    "Diagnosis_2": "diagnosis_2_code",
    "Other_Diagnosis_2": "other_diagnosis_2",
    "Disposition": "disposition_code",
    "Location": "location_code",
    "Fire_Involvement": "fire_involvement_code",
    "Product_1": "product_1",
    "Product_2": "product_2",
    "Product_3": "product_3",
    "Alcohol": "alcohol_code",
    "Drug": "drug_code",
    "Narrative_1": "narrative",
    "Stratum": "stratum",
    "PSU": "psu",
    "Weight": "weight",
}


class HeaderDriftError(Exception):
    """Raised when a file's header does not exactly match RAW_COLUMNS."""

    def __init__(self, year: int, expected: tuple[str, ...], actual: tuple[str, ...]):
        self.year = year
        self.expected = expected
        self.actual = actual
        super().__init__(
            f"Year {year}: header drift. Expected {list(expected)!r}, got {list(actual)!r}"
        )


class MalformedRecordError(Exception):
    """Raised when a record cannot be framed into exactly NCOLS fields after rejoin."""

    def __init__(self, year: int, detail: str):
        self.year = year
        self.detail = detail
        super().__init__(f"Year {year}: malformed record - {detail}")


class TypeCoercionError(MalformedRecordError):
    """Raised when a raw field value fails its strict typed cast during normalization."""

    def __init__(self, year: int, column: str, value: str):
        self.column = column
        self.value = value
        detail = f"column {column!r} value {value!r} failed strict cast"
        super().__init__(year, detail)
