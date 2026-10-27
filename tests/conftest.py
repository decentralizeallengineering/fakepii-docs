import json
import shutil
from pathlib import Path

import pytest

from fakepii_docs import FORMATS, generate_corpus

SEED = 42


@pytest.fixture(scope="session")
def corpus(tmp_path_factory) -> dict:
    """fmt -> {"doc": Path, "truth": Path, "data": dict} for the fixed seed."""
    out = tmp_path_factory.mktemp("corpus")
    manifest = generate_corpus(out, SEED)
    result = {}
    for entry in manifest["documents"]:
        truth = out / entry["truth"]
        result[entry["format"]] = {"doc": out / entry["document"], "truth": truth,
                                   "data": json.loads(truth.read_text("utf-8"))}
    assert set(result) == set(FORMATS)
    return result


@pytest.fixture
def work(tmp_path) -> Path:
    return tmp_path


def copy(src: Path, dst: Path) -> Path:
    shutil.copyfile(src, dst)
    return dst
