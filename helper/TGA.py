import difflib
import os
import re
import ast

import yaml
import numpy as np
import pandas as pd
import gspread
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, Any, Type, Callable, Union, Tuple
from dataclasses import dataclass, field
from matplotlib import pyplot as plt
from pandas.core.interchange.dataframe_protocol import DataFrame
from scipy.signal import butter, filtfilt, savgol_filter
from scipy.interpolate import interp1d
from gspread_dataframe import get_as_dataframe

from setting import get_google_credentials

# -----------------------------------------------------------------------------
# Import custom Filter classes
# -----------------------------------------------------------------------------
from helper.Filter import (
    ButterworthFilter,
    SavitzkyGolayFilter,
    ExponentialMovingAverage,
    MedianFilter,
    GaussianFilter,
    RollingAverage,
    MovingAverageDecimator
)

# -----------------------------------------------------------------------------
# Configuration dataclasses and factory
# -----------------------------------------------------------------------------

FILTER_REGISTRY: Dict[str, Type] = {
    "Butterworth": ButterworthFilter,
    "SavitzkyGolay": SavitzkyGolayFilter,
    "EMA": ExponentialMovingAverage,
    "Median": MedianFilter,
    "Gaussian": GaussianFilter,
    "RollingAverage": RollingAverage,
    "MovingAverage": MovingAverageDecimator,
}

import inspect
import warnings
import json
from dataclasses import dataclass, field
from typing import Dict, Any, Callable


@dataclass
class FilterConfig:
    use: str
    type: str
    params: Dict[str, Any] = field(default_factory=dict)

    def build(self) -> Callable:
        cls = FILTER_REGISTRY.get(self.type)
        if cls is None:
            valid = ", ".join(FILTER_REGISTRY.keys())
            raise ValueError(f"Unknown filter type '{self.type}'. Valid types: {valid}")

        sig = inspect.signature(cls.__init__)
        valid_params = {
            k: v for k, v in self.params.items()
            if k in sig.parameters and k != 'self'
        }

        try:
            return cls(**valid_params)
        except Exception as e:
            warnings.warn(f"[Warning] Failed to initialize {self.type} with params {valid_params}. Error: {e}")
            return cls()  # fallback to default constructor if possible

def theoretical_mass_loss_from_composition(
    comp: Dict[str, float],
    total_mass_g: float,
    *,
    fe_stage: str = "Fe",          # "Fe3O4" | "FeO" | "Fe"
    pb_mode: str = "retain",       # "retain" | "evaporate" | "chlorinate"
    zn_fraction: float = 1.0,      # fraction of ZnO volatilizing as Zn(g), 0..1
    pb_fraction: float = 1.0       # fraction of PbO affected by pb_mode, 0..1
) -> Dict[str, Any]:
    """
    Compute the theoretical mass loss (g and wt%) for one dust composition
    at ~1200 °C in a CO/CO2 (Boudouard) environment.

    `comp` may contain wt% or mass fractions (0..1). Keys can include:
    Al2O3, Br, CaO, Cl, Cr2O3, CuO, Fe2O3, K2O, MgO, MnO, MoO3, Na2O, NiO,
    P2O5, PbO, S, Sb2O3, SiO2, SnO2, TiO2, V2O5, WO3, ZnO

    Returns:
      {
        "mass_loss_g": float,
        "mass_loss_pct": float,
        "breakdown_g": {species_or_path: grams_lost, ...}
      }
    """

    # ----- molar masses (g/mol) -----
    M = {
        # elements
        "O": 16.00, "Cl": 35.45, "S": 32.065, "Br": 79.904,
        "Zn": 65.38, "Pb": 207.2,
        # oxides and salts
        "Al2O3": 101.961, "CaO": 56.077, "Cr2O3": 151.989, "CuO": 79.545,
        "Fe2O3": 159.690, "K2O": 94.196, "MgO": 40.304, "MnO": 70.937,
        "MoO3": 143.947, "Na2O": 61.979, "NiO": 74.692, "P2O5": 141.944,
        "PbO": 223.200, "Sb2O3": 291.517, "SiO2": 60.084, "SnO2": 150.708,
        "TiO2": 79.866, "V2O5": 181.878, "WO3": 231.837, "ZnO": 81.379,
        "PbCl2": 278.106,  # for chlorination path
    }

    # Always-volatile at ~1200 °C (remove full molar mass)
    ALWAYS_VOLATILIZE = {
        "Br", "Cl", "S", "K2O", "Na2O", "MoO3", "V2O5", "Sb2O3", "P2O5"
    }

    # Species we treat as non-volatile (mass stays) in this model
    NON_VOLATILE = {
        "Al2O3", "CaO", "Cr2O3", "CuO", "MgO", "MnO", "NiO",
        "SiO2", "SnO2", "TiO2", "WO3"  # WO3 set non-volatile by default here
    }

    # Normalize composition to mass fractions (0..1)
    tot = float(sum(comp.values())) if comp else 0.0
    as_percent = tot > 1.5   # heuristic: >1.5 means probably wt%
    def wf(k: str) -> float:
        v = float(comp.get(k, 0.0) or 0.0)
        return (v/100.0) if as_percent else v

    # Convert each key we know into grams and moles
    grams = {}
    moles = {}
    for k in set(M.keys()).intersection(comp.keys()):
        w = wf(k)
        grams[k] = w * total_mass_g
        moles[k] = grams[k] / M[k] if grams[k] > 0 else 0.0

    loss_g = 0.0
    breakdown: Dict[str, float] = {}

    # ---- Fe2O3: oxygen-only loss according to target stage ----
    n_Fe2O3 = moles.get("Fe2O3", 0.0)
    if n_Fe2O3:
        if fe_stage == "Fe3O4":
            d = 5.333 * n_Fe2O3                 # g O removed per mol Fe2O3
        elif fe_stage == "FeO":
            d = 16.000 * n_Fe2O3
        elif fe_stage == "Fe":
            d = 48.000 * n_Fe2O3
        else:
            raise ValueError("fe_stage must be 'Fe3O4', 'FeO', or 'Fe'")
        loss_g += d
        breakdown["Fe2O3_reduction"] = d

    # ---- Species that fully volatilize at 1200 °C ----
    for sp in ALWAYS_VOLATILIZE:
        n = moles.get(sp, 0.0)
        if n:
            d = M[sp] * n
            loss_g += d
            breakdown[f"{sp}_volatilize"] = d

    # ---- ZnO: volatilize as Zn(g) after reduction (fraction-controlled) ----
    n_ZnO = moles.get("ZnO", 0.0)
    if n_ZnO and zn_fraction > 0:
        n_act = n_ZnO * max(0.0, min(zn_fraction, 1.0))
        d = M["ZnO"] * n_act     # remove entire ZnO mass (practical proxy)
        loss_g += d
        breakdown["Zn_path"] = d

    # ---- PbO: retain / evaporate / chlorinate (fraction-controlled) ----
    n_PbO = moles.get("PbO", 0.0)
    n_Cl  = moles.get("Cl", 0.0)  # available chlorine for chlorination
    if n_PbO and pb_fraction > 0:
        n_act = n_PbO * max(0.0, min(pb_fraction, 1.0))
        if pb_mode == "retain":
            # Pb stays; only O leaves (1 O per PbO)
            d = 16.000 * n_act
            loss_g += d
            breakdown["Pb_reduction_O_only"] = d
        elif pb_mode == "evaporate":
            d = M["PbO"] * n_act
            loss_g += d
            breakdown["PbO_volatilize"] = d
        elif pb_mode == "chlorinate":
            # PbO + 2 Cl → PbCl2(g) + 0.5 O2  (proxy stoichiometry for bookkeeping)
            n_pbcl2 = min(n_act, n_Cl/2.0)
            if n_pbcl2 > 0:
                d = M["PbCl2"] * n_pbcl2
                loss_g += d
                breakdown["Pb_chlorinate_to_PbCl2"] = d
                # consume the Cl so we don't also count it as residual volatilization
                moles["Cl"] = max(0.0, n_Cl - 2.0*n_pbcl2)
            # Any remaining (un-chlorinated) PbO follows "retain" behavior (oxygen only)
            n_left = n_act - n_pbcl2
            if n_left > 0:
                d2 = 16.000 * n_left
                loss_g += d2
                breakdown["Pb_reduction_O_only_residual"] = d2
        else:
            raise ValueError("pb_mode must be 'retain', 'evaporate', or 'chlorinate'")

    # ---- Residual elemental Cl (not used for chlorination): volatilizes fully ----
    n_Cl_res = moles.get("Cl", 0.0)
    if n_Cl_res:
        d = M["Cl"] * n_Cl_res
        loss_g += d
        breakdown["Cl_residual"] = breakdown.get("Cl_residual", 0.0) + d

    # ---- Br and S already handled in ALWAYS_VOLATILIZE

    # ---- Everything tagged NON_VOLATILE produces no additional loss here ----
    # (Left explicit for clarity—nothing to do.)

    mass_loss_pct = (100.0 * loss_g / total_mass_g) if total_mass_g > 0 else np.nan
    return {"mass_loss_g": loss_g, "mass_loss_pct": mass_loss_pct, "breakdown_g": breakdown}

@dataclass
class CutReactiveConfig:
    """
    Configuration for cutting the reactive segment of the data.
    """
    lower_temp: float = None
    upper_temp: float = None


@dataclass
class CutTailConfig:
    """
    Configuration for cutting the tail of the data.
    """
    enabled: bool = False
    threshold: float = -0.8
    buffer: int = 200


@dataclass
class DustComposition:
    wt_pct: Dict[str, float]  # e.g., {"Fe": 28.3, "Zn": 20.1, ...}

    def normalized(self) -> "DustComposition":
        s = sum(v for v in self.wt_pct.values() if v is not None)
        if s and not np.isclose(s, 100.0):
            return DustComposition({k: (v / s) * 100.0 for k, v in self.wt_pct.items()})
        return self


@dataclass
class PreparationConfig:
    process_file: str = "yes"
    pre_filter: Optional[FilterConfig] = None
    post_filter: Optional[FilterConfig] = None
    cut_reactive: CutReactiveConfig = field(default_factory=CutReactiveConfig)
    cut_tail: CutTailConfig = field(default_factory=CutTailConfig)
    gas_columns: Dict[str, str] = field(default_factory=dict)

    @classmethod
    def load_from_file(cls, path: str) -> "PreparationConfig":
        with open(path, "r", encoding="utf-8") as f:
            raw: Dict[str, Any] = json.load(f)

        def _select_active_filter(filters_raw) -> Optional[FilterConfig]:
            if isinstance(filters_raw, list):
                active = [f for f in filters_raw if f.get("use", "").lower() == "yes"]
                if len(active) > 1:
                    print(f"[Warning] Multiple filters are marked 'use: yes'. Using only the first: {active[0].get('type')}")
                if active:
                    return FilterConfig(**active[0])
            elif isinstance(filters_raw, dict):
                if filters_raw.get("use", "").lower() == "yes":
                    return FilterConfig(**filters_raw)
            return None

        return cls(
            process_file=raw.get("process_file", "yes"),
            pre_filter=_select_active_filter(raw.get("pre_filter")),
            post_filter=_select_active_filter(raw.get("post_filter")),
            cut_reactive=CutReactiveConfig(**raw.get("cut_reactive", {})),
            cut_tail=CutTailConfig(**raw.get("cut_tail", {})),
            gas_columns=raw.get("gas_columns", {})
        )


# -----------------------------------------------------------------------------
# Google Sheets loader for metadata
# -----------------------------------------------------------------------------

class GoogleSheetLoader:
    """
    Loads metadata rows from a Google Sheets document.
    """

    def __init__(self, show_sheetnames: bool = False):
        self.gc = gspread.service_account(filename=get_google_credentials())
        self.sh = self.gc.open_by_url(self._get_spreadsheet_url())
        if show_sheetnames:
            self.print_sheet_names()

    def load_sheet(self, sheet_name: str) -> pd.DataFrame:
        try:
            ws = self.sh.worksheet(sheet_name)
            df = get_as_dataframe(ws, evaluate_formulas=True, header=0)
            return df.dropna(how='all')
        except Exception as e:
            print(f"[Error] Loading '{sheet_name}': {e}")
            return pd.DataFrame()

    def print_sheet_names(self) -> None:
        print("Available sheets:")
        for ws in self.sh.worksheets():
            print(f" - {ws.title}")

    @staticmethod
    def _get_spreadsheet_url() -> str:
        return r"https://docs.google.com/spreadsheets/d/1HooNjAziwRFESXFmY-s6S8lxb8Ztt_YAEoxR19NaE-Q/edit?gid=1985584655#gid=1985584655"


# -----------------------------------------------------------------------------
# TGAFile: load raw data and metadata
# -----------------------------------------------------------------------------

class TGAFile:
    """
    Parses TGA data from .csv or .parquet and extracts weight and start time.
    """

    def __init__(self, path: Union[str, Path]):
        self.path = Path(path)
        self.weight_available: bool = False
        self.df = self._load_data()

    def _load_data(self) -> pd.DataFrame:
        # Parquet
        if self.path.suffix == '.parquet':
            return pd.read_parquet(self.path)
        # CSV with header discovery
        with open(self.path, encoding='ISO-8859-1') as f:
            for i, line in enumerate(f):
                if 'Weight' in line:
                    self.weight_available = True
                if line.startswith('Time(s)'):
                    return pd.read_csv(
                        self.path,
                        delimiter=',',
                        header=i,
                        encoding='unicode_escape'
                    )

        raise ValueError(f"Header 'Time(s)' not found in {self.path}")

    def get_weight(self) -> float:
        if not self.weight_available:
            return 0.0
        with open(self.path, encoding='ISO-8859-1') as f:
            for _ in range(3):
                next(f)
            line = next(f)
        m = re.search(r"Weight: ([0-9]+\.?[0-9]*) mg", line)
        return float(m.group(1)) if m else 0.0

    def get_experiment_start(self) -> Tuple[Optional[datetime], float]:
        translations = {
            'Mo': 'Mon', 'Di': 'Tue', 'Mi': 'Wed', 'Do': 'Thu',
            'Fr': 'Fri', 'Sa': 'Sat', 'So': 'Sun',
            'Jän': 'Jan', 'Feb': 'Feb', 'Mrz': 'Mar', 'Apr': 'Apr',
            'Mai': 'May', 'Jun': 'Jun', 'Jul': 'Jul', 'Aug': 'Aug',
            'Sep': 'Sep', 'Okt': 'Oct', 'Nov': 'Nov', 'Dez': 'Dec'
        }
        with open(self.path, encoding='ISO-8859-1') as f:
            for line in f:
                if line.startswith("# Measurement date and time:"):
                    date_str = line.split(": ")[1].strip()
                    for de, en in translations.items():
                        date_str = date_str.replace(de, en)
                    dt = datetime.strptime(date_str, "%a %b %d %H:%M:%S %Y")
                    return dt, dt.timestamp()
        return None, 0.0


# -----------------------------------------------------------------------------
# TGAExperiment: processing pipeline
# -----------------------------------------------------------------------------
class TGAExperiment:
    """
    End-to-end TGA data processing and plotting.
    """

    def __init__(
            self,
            file_path: Union[str, Path],
            config: Optional[PreparationConfig] = None,
            save_parquet: Optional[bool] = True,
            experiment_id: Optional[str] = None,
            df_comp: Optional[pd.DataFrame] = None,
            df_meta: Optional[pd.DataFrame] = None,
            df_corr: Optional[DataFrame] = None,
            parser: Callable = TGAFile
    ):
        self.file_path = Path(file_path)
        print(f'[Info] Working on file {self.file_path}')
        if not self.file_path.exists():
            raise FileNotFoundError(f"File not found: {self.file_path}")
        self.config = config
        self.experiment_id = experiment_id
        self.df_meta = df_meta
        self.df_comp = df_comp
        self.df_corr = df_corr
        self.metadata: Dict[str, Any] = {}
        self.dust_composition = dict()

        if df_comp is not None:
            self.dust_composition = self.load_dust_composition(dust_df=self.df_comp)

        # Path for cached parquet
        self._parquet_path = self.file_path.with_suffix('.parquet')

        # If cached processed data exists, load and skip processing
        if self._parquet_path.exists() and self.config.process_file.upper() != 'YES':
            self.df = pd.read_parquet(self._parquet_path)
            self.original_df = self.df.copy(deep=True)
            print(f"[Info] Loaded cached DataFrame from {self._parquet_path}\n")
            self.initial_weight = self.df.get('m_filtered_mg', 0)
            return

        # Load raw data
        self.parser = parser(self.file_path)
        self.df = self.parser.df.copy()
        self.original_df = self.df.copy(deep=True)

        # Extract metadata
        self.experiment_datetime, self.start_timestamp = self.parser.get_experiment_start()
        self.initial_weight = self.parser.get_weight()

        # Run processing
        self.load_and_process()

        # After processing, save to parquet
        if save_parquet:
            self._save_parquet()
        print('\n')

    def get_theoretical_mass_loss(
            self,
            *,
            fe_stage: str = "Fe",
            pb_mode: str = "retain",  # "retain" | "evaporate" | "chlorinate"
            zn_fraction: float = 1.0,  # 0..1 of ZnO that volatilizes as Zn
            pb_fraction: float = 1.0,  # 0..1 of PbO affected by pb_mode
    ) -> Dict[str, Any]:
        """
        Use the experiment's loaded dust composition and initial weight (mg)
        to compute theoretical mass loss under a 1200 °C Boudouard environment.

        Parameters mirror `theoretical_mass_loss_from_composition`.
        """
        if not self.dust_composition:
            raise ValueError("No dust composition available for this experiment.")
        if not hasattr(self, "initial_weight") or self.initial_weight is None:
            raise ValueError("Initial weight not available.")

        mass_g = float(self.initial_weight) / 1000.0  # mg → g
        return theoretical_mass_loss_from_composition(
            self.dust_composition,
            mass_g,
            fe_stage=fe_stage,
            pb_mode=pb_mode,
            zn_fraction=zn_fraction,
            pb_fraction=pb_fraction,
        )
    def _save_parquet(self) -> None:
        """Save processed DataFrame to Parquet in same directory."""
        self._parquet_path.parent.mkdir(parents=True, exist_ok=True)
        self.df.to_parquet(self._parquet_path)
        print(f"[Info] Saved processed DataFrame to {self._parquet_path}")

    def load_and_process(self) -> None:
        """
        Ensures continuous sampling, renames, drops unwanted columns, and runs prepare().
        """

        self._safe_rename_columns()
        if not self._is_continuous_sampling():
            self._make_sampling_continuous()
        self.df.drop(self._columns_to_drop(), axis=1, inplace=True, errors='ignore')
        self.df['time_abs'] = self.df[self._get_column_name('time')] + self.start_timestamp

        self.prepare()

    def load_metadata(self) -> pd.DataFrame:
        """
        Loads metadata from Google Sheet if loader and IDs are provided.
        """
        if self.experiment_id and self.df_meta is not None:
            df_meta_exp_id = self.find_rows_by_rt_id(self.df_meta)
            if not df_meta_exp_id.empty:
                return df_meta_exp_id
            print(f"[Warning] No metadata for ID {self.experiment_id}")
        return pd.DataFrame()

    def find_rows_by_rt_id(self, df_meta: pd.DataFrame) -> pd.DataFrame:
        if not any(col.lower() == 'id' for col in df_meta.columns):
            print(f"[Warning] 'id' column missing in DataFrame. Following columns are available:\n{df_meta.columns}")
            return pd.DataFrame()
        pattern = f'^{re.escape(self.experiment_id)}(_\\d+)?$'
        mask = df_meta['id'].fillna('').astype(str).str.match(pattern)
        return df_meta[mask].copy()

    def prepare(self) -> None:
        """
        Applies filters, segment cuts, tail cuts, and gas column mapping.
        Automatically builds any FilterConfig entries into filter instances.
        If the filter type or its params are invalid, emits a warning listing
        the valid types and/or expected parameters.
        """

        # --- Helper to warn about filter types ---
        def warn_invalid_type(name):
            valid = ", ".join(FILTER_REGISTRY.keys())
            print(f"[Warning] Unknown filter type '{name}'. Valid types are: {valid}")

        # --- Helper to warn about filter params ---
        def warn_invalid_params(filter_name, exc, sig):
            params = [p.name for p in list(sig.parameters.values())[1:]]
            print(f"[Warning] {filter_name} init failed: {exc}")
            print(f"          Expected parameters: {params}")

        # 1) Build pre_filter
        pf = self.config.pre_filter
        if hasattr(pf, 'type'):
            # wrong type name?
            if pf.type not in FILTER_REGISTRY:
                warn_invalid_type(pf.type)
                self.config.pre_filter = None
            else:
                cls = FILTER_REGISTRY[pf.type]
                sig = inspect.signature(cls.__init__)
                try:
                    self.config.pre_filter = pf.build()
                except Exception as e:
                    warn_invalid_params(pf.type, e, sig)
                    # fallback to defaults
                    defaults = {
                        p.name: (p.default if p.default is not inspect._empty else None)
                        for p in list(sig.parameters.values())[1:]
                    }
                    self.config.pre_filter = cls(**defaults)

        # 2) Build post_filter
        postf = self.config.post_filter
        if hasattr(postf, 'type'):
            if postf.type not in FILTER_REGISTRY:
                warn_invalid_type(postf.type)
                self.config.post_filter = None
            else:
                cls = FILTER_REGISTRY[postf.type]
                sig = inspect.signature(cls.__init__)
                try:
                    self.config.post_filter = postf.build()
                except Exception as e:
                    warn_invalid_params(postf.type, e, sig)
                    defaults = {
                        p.name: (p.default if p.default is not inspect._empty else None)
                        for p in list(sig.parameters.values())[1:]
                    }
                    self.config.post_filter = cls(**defaults)

        # 3) Continue pipeline
        self._convert_time()
        self._smooth_h2o()

        if self.df_corr is not None:
            self._calculate_corrected_delta()

        if 'dm' not in self.df.columns:
            raise ValueError("Missing 'dm' column for mass computation")
        self.df.rename(columns={'dm': 'dm_original_mg'}, inplace=True)

        len_before = len(self.df)
        # Pre-filter application
        if self.config.pre_filter:
            try:
                if isinstance(self.config.pre_filter, MovingAverageDecimator):
                    self.apply_dataframe_filter(self.config.pre_filter, 'time_min')
                    self.df['dm_filtered_mg'] = self.df['dm_original_mg'].copy()
                else:
                    self.apply_filter(self.config.pre_filter,
                                      'time_min', 'dm_original_mg',
                                      'dm_filtered_mg')
            except Exception as e:
                print(f"[Warning] Prefilter application failed: {e}")
        else:
            self.df['dm_filtered_mg'] = self.df['dm_original_mg'].copy()
        len_prefilter = len(self.df)
        if 'CORRECTION' in str(self.file_path).upper():
            return

        self._calc_rel_mass('dm_original_mg', 'dm_original_pct')
        self._calc_rel_mass('dm_filtered_mg', 'dm_filtered_pct')

        self.derive_column('dm_filtered_pct', 'dmdt_filtered_pctmin')
        self.derive_column('dm_filtered_mg', 'dmdt_filtered_mgmin')
        self.derive_column('dm_original_pct', 'dmdt_original_pctmin')
        self.derive_column('dm_original_mg', 'dmdt_original_mgmin')

        # --- create absolute mass from filtered dm ---
        self.df['m_filtered_mg'] = self.initial_weight + self.df['dm_filtered_mg']
        # Ensure the first value is exactly the initial weight
        self.df.loc[self.df.index[0], 'm_filtered_mg'] = self.initial_weight

        # Post-filter application
        if self.config.post_filter:
            try:
                if isinstance(self.config.post_filter, MovingAverageDecimator):
                    self.apply_dataframe_filter(self.config.post_filter, 'time_min')
                else:
                    self.apply_filter(self.config.post_filter,
                                      'time_min', 'dmdt_filtered_pctmin',
                                      'dmdt_filtered_pctmin')
                    self.apply_filter(self.config.post_filter,
                                      'time_min', 'dmdt_filtered_mgmin',
                                      'dmdt_filtered_mgmin')
            except Exception as e:
                print(f"[Warning] Post-filter application failed: {e}")

        len_postfilter = len(self.df)

        print(f'Before filter:\t{len_before}\nPrefilter:\t{len_prefilter}\nPostfilter:\t{len_postfilter}')


        self.cut_and_rename()

    def cut_and_rename(self):
        # Reactive segment cut
        if self.config.cut_reactive:
            cr = self.config.cut_reactive
            self.df = self.cut_reactive_segment(cr.lower_temp, cr.upper_temp)

        # Tail cut
        if self.config.cut_tail.enabled:
            ct = self.config.cut_tail
            self.df = self.cut_tail(ct.threshold, ct.buffer)

        # Gas column remapping (only if source exists)
        for src, tgt in self.config.gas_columns.items():
            if src in self.df.columns:
                self.df[tgt] = self.df[src]
                self.df.drop(columns=src, inplace=True, errors='ignore')

    def derive_column(self, src: str, tgt: str) -> None:
        """
        Compute first derivative of src vs time_min into tgt.
        """
        self.df[tgt] = self.df[src].diff() / self.df['time_min'].diff()
        self.df[tgt] = self.df[tgt].bfill()

    def cut_reactive_segment(
            self,
            lower_temp: Optional[float] = None,
            upper_temp: Optional[float] = None
    ) -> pd.DataFrame:
        """
        Trim the DataFrame based on when temperature first exceeds lower_temp
        and/or first exceeds upper_temp.

        Parameters
        ----------
        lower_temp : float or None
            If not None, drop everything *before* the first crossing of lower_temp.
        upper_temp : float or None
            If not None, drop everything *after*  the first crossing of upper_temp.

        Returns
        -------
        pd.DataFrame
            A trimmed copy of `self.df`.
        """

        # 1) No temperature column?  Bail out early.
        if 'temperature_C' not in self.df.columns:
            return self.df.copy()

        temps = self.df['temperature_C']

        # 2) Determine start index
        if lower_temp is not None and (temps > lower_temp).any():
            start_idx = temps.gt(lower_temp).idxmax()
        else:
            # either no lower_temp specified, or it never crosses → start at first row
            start_idx = self.df.index[0]

        # 3) Determine end index
        if upper_temp is not None and (temps > upper_temp).any():
            end_idx = temps.gt(upper_temp).idxmax()
        else:
            # either no upper_temp specified, or it never crosses → end at last row
            print(
                f'[Warning] Upper temperature in configuration file exceeds the maximum temperature {max(temps)} °C of'
                f'the experiment!')
            end_idx = self.df.index[-1]

        # 4) Convert labels to integer positions
        pos_start = self.df.index.get_loc(start_idx)
        pos_end = self.df.index.get_loc(end_idx)

        # 5) If the crossing order is “inverted”, return empty
        if pos_start > pos_end:
            return pd.DataFrame(columns=self.df.columns)

        # 6) Slice (add +1 so that the end-row itself is included)
        return self.df.iloc[pos_start: pos_end + 1].reset_index(drop=True)

    def cut_tail(self, threshold: float, buffer: int) -> pd.DataFrame:
        """
        Cut off the tail based on second derivative dropping below threshold.
        """
        name = 'd2dt2'
        self.derive_column('dmdt_filtered_pctmin', name)
        window = self.df[name].iloc[-buffer:]
        idxs = window[window < threshold].index
        if len(idxs):
            return self.df.loc[:idxs[0]]
        return self.df

    def plot_experiment(self, plot_show: bool = False, save_dir: Optional[Path] = None, row_meta: DataFrame = None) -> None:
        """
        Generates a 3-panel plot of mass, kinetics, temperature, and gas flows.
        """
        # Rename for clarity
        y1, y2 = 'dm_original_pct', 'dm_filtered_pct'
        y3, y4 = 'dmdt_original_pctmin', 'dmdt_filtered_pctmin'

        fig, (ax1, ax2, ax3) = plt.subplots(3, 1, sharex=True, figsize=(7, 10))
        fig.suptitle(self.experiment_id)
        ax_gas = ax3.twinx()

        # Mass
        ax1.plot(self.df['time_min'], self.df[y1], color='grey', linewidth=.1)
        ax1.plot(self.df['time_min'], self.df[y2], color='C1')
        ax1.set_ylabel('Relative Mass [%]')
        ax1.legend(['orig', 'filt'], frameon=False)

        # Kinetics
        ax2.plot(self.df['time_min'], self.df[y3], color='grey', linewidth=.1)
        ax2.plot(self.df['time_min'], self.df[y4])
        ax2.axhline(0, linestyle='--', linewidth=0.5, color='grey')
        ax2.set_ylim(min(self.df[y4]) - 0.5, max(self.df[y4]) + 0.5)
        ax2.set_ylabel('Relative Reaction Kinetics [%/min]')
        ax2.legend(['orig', 'filt'], frameon=False)

        # Temp and gases
        ax3.plot(self.df['time_min'], self.df['temperature_C'], lw=0.8, color='black',linestyle='dashed', label='Temperature')
        for gas in self.config.gas_columns.values():
            if gas in self.df.columns:
                if gas == 'H2O':
                    ax_gas.plot(self.df['time_min'], self.df['H2O'], lw=0.7, label=gas)
                else:
                    ax_gas.plot(self.df['time_min'], self.df.get(gas, []), lw=0.7, label=gas)
            else:
                continue
        ax3.set_ylabel('Temperature [°C]')
        ax_gas.set_ylabel('Flowrate Gas [ml/min]')

        handles1, labels1 = ax3.get_legend_handles_labels()
        handles2, labels2 = ax_gas.get_legend_handles_labels()
        ax3.legend(handles1 + handles2, labels1 + labels2, frameon=False)

        ax_gas.set_ylim([0, 100])

        if save_dir:
            material = row_meta['material']
            temp = int(row_meta['Tmax'])
            gas = row_meta['Gas'].split('/')[0]
            if material not in os.listdir(save_dir):
                os.mkdir(os.path.join(save_dir, material))

            save_name = fr'{os.path.join(save_dir, material)}/{self.experiment_id}_{material}_{temp}_{gas}.png'
            fig.savefig(save_name, dpi=300)

        if plot_show:
            plt.show()
        plt.close(fig)

    # ----------------------- helper methods -----------------------
    @staticmethod
    def _columns_to_drop() -> list:
        return [
            'POWER(%)', 'TEMP_CJR_FURNACE(K)', 'TEMP_CJR_SAMPLE(K)',
            'TEMP_FURNACE(K)', 'TEMP_NOM_FURNACE(K)', 'TEMP_NOM_SAMPLE(K)',
            'TGA_RAW(mg)'
        ]

    @staticmethod
    def _column_rename_map() -> Dict[str, str]:
        return {
            'Time(s)': 'time',
            'Gas 1(sccm/min)': 'Gas1',
            'Gas 2(sccm/min)': 'Gas2',
            'Purge(sccm/min)': 'Purge',
            'Temperature(°C)': 'temperature_C',
            'Wasser(ml/min)': 'Water',
            'Delta m(mg)': 'dm',
            'Corrected delta m(mg)': 'dm'
        }

    def _safe_rename_columns(self) -> None:
        rmap = self._column_rename_map()
        existing = {k: v for k, v in rmap.items() if k in self.df.columns}
        self.df.rename(columns=existing, inplace=True)

    def _is_continuous_sampling(self) -> bool:
        col = self._get_column_name(col_guess='time')
        if col.startswith("No similar column"):
            raise KeyError(col)  # or just return False
        diffs = self.df[col].diff().dropna().round(10)
        return diffs.nunique() == 1

    def _make_sampling_continuous(self) -> None:
        old = self.df['time']
        step = np.round(old.diff().median(), 3)
        new_time = np.arange(old.min(), old.max(), step)
        new_df = pd.DataFrame({'time': new_time})
        for col in self.df.columns.drop('time'):
            interp = interp1d(old, self.df[col], fill_value='extrapolate')
            new_df[col] = interp(new_time)
        self.df = new_df

    def _convert_time(self) -> None:
        time_col = self._get_column_name('time')
        self.df[time_col] = self.df[time_col] / 60
        self.df.rename(columns={time_col: 'time_min'}, inplace=True)

    def _calc_rel_mass(self, col: str, new_col: str) -> None:
        self.df[new_col] = (self.df[col] + self.initial_weight) / self.initial_weight * 100

    def apply_filter(
            self, filt: Callable, x_col: str, y_col: str, result_col: Optional[str] = None
    ) -> None:
        if result_col is None:
            result_col = f"{y_col}_{filt}"
        self.df[result_col] = filt(self.df[x_col], self.df[y_col])

    def apply_dataframe_filter(self, filt: Callable, x_col: str) -> None:
        self.df = filt(self.df, x_col)

    def adapt_correction_curve(self, correction_df: Optional[pd.DataFrame]) -> None:
        if correction_df is None:
            return
        if 'Corrected delta m(mg)' in self.df.columns:
            self.df.rename(columns={'Corrected delta m(mg)': 'dm'}, inplace=True)
        else:
            merged = pd.merge(
                self.df,
                correction_df[['time', 'dm']],
                on='time',
                how='inner',
                suffixes=('_tga', '_corr')
            )
            merged['dm'] = merged['dm_tga'] - merged['dm_corr']
            self.df = merged

    def __getitem__(self, item):
        return self.df[item]

    def get_max_temperature(self) -> float:
        return float(self.df['temperature_C'].max())

    def _calculate_corrected_delta(self):
        if self.df_corr is None or 'Corrected delta m(mg)' in self.df.columns:
            return

        # Check columns
        if 'dm' not in self.df.columns or 'time_min' not in self.df.columns:
            raise ValueError("self.df must have 'dm' and 'time_min' columns")
        if 'dm_original_mg' not in self.df_corr.columns or 'time_min' not in self.df_corr.columns:
            raise ValueError("df_corr must have 'dm_original_mg' and 'time_min' columns")

        # Check sampling intervals
        self_step = self.df['time_min'].diff().median()
        corr_step = self.df_corr['time_min'].diff().median()
        sampling_rate_same = np.isclose(self_step, corr_step, rtol=1e-4)

        # Interpolate df_corr if sampling rate is different
        if not sampling_rate_same or len(self.df) != len(self.df_corr):
            # Interpolate correction to match self.df's time_min
            interp_func = interp1d(
                self.df_corr['time_min'].values,
                self.df_corr['dm_original_mg'].values,
                kind='linear',
                bounds_error=False,
                fill_value=(self.df_corr['dm_original_mg'].iloc[0], self.df_corr['dm_original_mg'].iloc[-1])
            )
            correction = interp_func(self.df['time_min'].values)
        else:
            # Sampling rate is the same, so values should align directly
            # But ensure indices match!
            # If the indices are off but the times match, align on time_min:
            correction = pd.Series(self.df_corr['dm_original_mg'].values, index=self.df_corr['time_min'])
            correction = correction.reindex(self.df['time_min']).values

        # Sum at each timestamp
        self.df['Corrected delta m(mg)'] = self.df['dm'].values - correction

    def load_dust_composition(self, dust_df: pd.DataFrame) -> None:
        """
        Get the dust composition for this experiment based on df_meta['Material'].
        Stores it in self.composition and self.metadata['composition'].
        """
        if self.df_meta is None or self.df_meta.empty:
            print("[Warning] No metadata DataFrame loaded — cannot map composition.")
            return

        # Find the Material value for this experiment
        df_meta_row = self.find_rows_by_rt_id(self.df_meta)
        if df_meta_row.empty:
            print(f"[Warning] No metadata for experiment ID {self.experiment_id}")
            return

        material_name = df_meta_row.iloc[0].get("material")
        if pd.isna(material_name):
            print(f"[Warning] 'Material' missing for experiment {self.experiment_id}")
            return

        # Find matching dust row in dust_df
        match = dust_df.loc[dust_df["Dust"] == material_name]
        if match.empty:
            print(f"[Warning] No composition found for Material '{material_name}'")
            return

        # Convert to dict excluding the Dust column
        comp_dict = match.drop(columns=["Dust"]).iloc[0].to_dict()

        return comp_dict

    def _get_column_name(self,  col_guess: str) -> str:
        """
        Find the closest matching column name in self.df.columns to 'col_guess'.
        If no similar column exists, return a message.

        :param col_guess: guessed column name to search for
        :return: best-matching column name or a fallback string
        """
        columns = self.df.columns
        matches = difflib.get_close_matches(col_guess, columns, n=1, cutoff=0.6)
        if matches:
            return matches[0]
        return f"No similar column found for '{col_guess}'"

    def _smooth_h2o(self):

        if ('Water' in self.df.columns) & (~self.df['Water'].empty):
            # Estimate sampling rate
            fs =1 / (self.df['time_min'].diff().median() * 60)

            # Choose cutoff frequency (Hz)
            f_c = 0.05  # Keep signals below 0.01 Hz

            # Normalize cutoff
            Wn = f_c / (fs / 2)

            # Design Butterworth filter
            self.df['Water'] = self.df['Water']*1240
            [b, a] = butter(N=2, Wn=Wn, btype='low', fs=fs)
            self.df['Water'] = filtfilt(b, a, self.df['Water'])
        else:
            return

# -----------------------------------------------------------------------------
# Example usage
# -----------------------------------------------------------------------------

if __name__ == '__main__':
    # Load preparation config
    prep_cfg = PreparationConfig.load_from_file('config.json')

    # Optionally initialize GoogleSheetLoader
    sheet_loader = GoogleSheetLoader(show_sheetnames=False)

    # Run and plot
    exp = TGAExperiment(
        file_path='data/sample.csv',
        config=prep_cfg,
        experiment_id='RT17',
        sheet_loader=sheet_loader,
        sheet_name='Experiments'
    )
    exp.plot_experiment(plot_show=True)
