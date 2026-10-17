"""Temporal blocks and label-availability cut-offs."""

from datetime import datetime

import pandas as pd
import pytest

from delivery_risk.config import BLOCKS, Block, Splits, load_splits, validate_splits
from delivery_risk.modeling.data import assign_blocks, fit_mask


@pytest.mark.parametrize("mode", ["real", "test", "e2e"])
def test_configured_splits_are_contiguous_and_disjoint(mode):
    s = load_splits(mode)
    assert [b.name for b in s.blocks] == list(BLOCKS)
    for a, b in zip(s.blocks, s.blocks[1:], strict=False):
        assert a.end == b.start  # contiguous, half-open -> disjoint
    assert len(s.sha256) == 64


def test_overlap_is_rejected():
    blocks = (
        Block("train", datetime(2018, 1, 1), datetime(2018, 2, 1)),
        Block("selection", datetime(2018, 1, 15), datetime(2018, 3, 1)),
        Block("calibration", datetime(2018, 3, 1), datetime(2018, 4, 1)),
        Block("test", datetime(2018, 4, 1), datetime(2018, 5, 1)),
    )
    with pytest.raises(ValueError):
        validate_splits(Splits("x", "frozen", blocks))


def test_hash_changes_with_boundaries():
    s = load_splits("e2e")
    moved = Splits(
        s.mode,
        s.status,
        (Block("train", s.blocks[0].start, datetime(2017, 11, 30)),) + s.blocks[1:],
    )
    assert moved.sha256 != s.sha256


def _frame():
    return pd.DataFrame(
        {
            "order_id": ["a", "b", "c", "d", "e"],
            "prediction_at": pd.to_datetime(
                ["2017-11-20", "2017-11-28", "2017-11-29", "2017-12-05", "2017-11-10"]
            ),
            "label_eligible": [True, True, True, True, False],
            "label_available_at": pd.to_datetime(
                [
                    "2017-11-27 10:00",
                    "2017-12-02 10:00",
                    "2017-11-30 23:59",
                    "2017-12-10 10:00",
                    None,
                ]
            ),
        }
    )


def test_fit_mask_excludes_labels_not_yet_available_at_cutoff():
    f = _frame()
    f["block"] = assign_blocks(f, load_splits("e2e"))
    m = fit_mask(f, ("train",), datetime(2017, 12, 1))
    # a: delivered before cutoff -> used; b: approved in train but delivered after cutoff -> NOT used;
    # c: delivered just before cutoff -> used; d: other block; e: no eligible label
    assert m.tolist() == [True, False, True, False, False]


def test_fit_mask_cutoff_is_strict():
    f = _frame()
    f.loc[0, "label_available_at"] = pd.Timestamp("2017-12-01 00:00:00")
    f["block"] = assign_blocks(f, load_splits("e2e"))
    assert not fit_mask(f, ("train",), datetime(2017, 12, 1)).iloc[0]
