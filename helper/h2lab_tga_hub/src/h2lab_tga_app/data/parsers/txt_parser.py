from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
from h2lab_tga_app.infra.repo_root import find_repo_root


def parse_txt(path: Path) -> pd.DataFrame:
    repo_root = find_repo_root(Path(__file__).resolve())
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))

    from helper.TGA import TGAFile  # pylint: disable=import-outside-toplevel

    parser = TGAFile(path)
    return parser.df
