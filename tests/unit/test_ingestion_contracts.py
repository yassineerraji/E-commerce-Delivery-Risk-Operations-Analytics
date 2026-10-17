"""CSV header contracts, record iteration and synthetic generator determinism."""

from pathlib import Path

import pytest
from support.synthetic import generate_to_directory

from delivery_risk.ingestion.contracts import CONTRACTS, CONTRACTS_BY_DATASET
from delivery_risk.ingestion.load_raw import (
    HeaderMismatch,
    MissingSourceFiles,
    check_files,
    iter_records,
    read_header,
    sha256_of,
    validate_header,
)

FIXTURES = Path(__file__).parents[1] / "fixtures" / "edge_cases"


def test_every_fixture_matches_its_contract():
    for c in CONTRACTS:
        positions = validate_header(c, read_header(FIXTURES / c.file_name))
        assert len(positions) == len(c.columns)


def test_bom_is_stripped_from_header():
    header = read_header(FIXTURES / "product_category_name_translation.csv")
    assert header[0] == "product_category_name"


def test_reordered_columns_are_mapped_by_name():
    c = CONTRACTS_BY_DATASET["sellers"]
    positions = validate_header(
        c, ["seller_state", "seller_id", "seller_city", "seller_zip_code_prefix"]
    )
    assert positions == [1, 3, 2, 0]


@pytest.mark.parametrize(
    "header",
    [
        ["seller_id", "seller_zip_code_prefix", "seller_city"],  # missing column
        [
            "seller_id",
            "seller_zip_code_prefix",
            "seller_city",
            "seller_state",
            "x",
        ],  # unexpected column
    ],
)
def test_header_mismatch_stops_the_load(header):
    with pytest.raises(HeaderMismatch):
        validate_header(CONTRACTS_BY_DATASET["sellers"], header)


def test_missing_files_are_listed_not_substituted(tmp_path):
    (tmp_path / "olist_orders_dataset.csv").write_text("x\n")
    with pytest.raises(MissingSourceFiles) as exc:
        check_files(tmp_path)
    assert "olist_sellers_dataset.csv" in str(exc.value)
    assert "olist_orders_dataset.csv" not in exc.value.missing


def test_multiline_record_keeps_its_start_line():
    c = CONTRACTS_BY_DATASET["reviews"]
    path = FIXTURES / c.file_name
    records = list(iter_records(path, validate_header(c, read_header(path))))
    by_id = {values[0]: line for line, _, values in records}
    # r07b starts on line 8 and spans two physical lines, so r08 starts on line 10.
    assert by_id["r07b"] == 8 and by_id["r08"] == 10
    msg = next(v for _, _, v in records if v[0] == "r07b")[4]
    assert "\n" in msg and "," in msg


def test_wrong_field_count_is_flagged_not_dropped(tmp_path):
    c = CONTRACTS_BY_DATASET["sellers"]
    p = tmp_path / c.file_name
    p.write_text(
        "seller_id,seller_zip_code_prefix,seller_city,seller_state\ns1,01000,sp,SP\ns2,01000,SP\n"
    )
    rows = list(iter_records(p, validate_header(c, read_header(p))))
    assert len(rows) == 2 and rows[1][1] == "field_count:3" and rows[1][2] == [None] * 4


def test_synthetic_generator_is_deterministic(tmp_path):
    generate_to_directory(tmp_path / "a", n_orders=400)
    generate_to_directory(tmp_path / "b", n_orders=400)
    for c in CONTRACTS:
        assert sha256_of(tmp_path / "a" / c.file_name) == sha256_of(tmp_path / "b" / c.file_name)
