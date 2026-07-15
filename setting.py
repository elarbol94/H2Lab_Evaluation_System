from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Literal, TypeAlias

from matplotlib.axes import Axes
from matplotlib.figure import Figure
from matplotlib.legend import Legend


ENV_KEY = "H2LAB_SHAREPOINT_PATH"
DEFAULT_SHAREPOINT_PATH = Path(
    r"C:\Users\aaron\OneDrive - HydrogenReductionLab\Hydrogen Reductionlab - sharepoint\H2Lab"
)

PLOT_SIZE_ENV_KEY = "PUB259_PLOT_SIZE_IN"
DEFAULT_PLOT_SIZE_IN = (23.18 / 2.54, 13.04 / 2.54)  # 16:9 slide-friendly size in inches
PAPER_TEXT_WIDTH_CM = 15.92
PAPER_HALF_WIDTH_CM = PAPER_TEXT_WIDTH_CM / 2.0
PAPER_ASPECT_RATIO = 0.62
PUB259_PROJECT_FOLDER = "PUB_25_9 Lime in EAFD Recycling"
FIGURE_TARGET_CHOICES = ("paper", "presentation")
FIGURE_DOMAIN_CHOICES = ("EMI", "TGA", "SEM", "Composition", "analysis")
FigureTarget: TypeAlias = Literal["paper", "presentation"]
FigureDomain: TypeAlias = Literal["EMI", "TGA", "SEM", "Composition", "analysis"]

# All font-related defaults are configured here (no env override).
DEFAULT_PLOT_FONT = "Arial"
fontsize = 11
DEFAULT_FONT_SIZE = fontsize
DEFAULT_AXES_LABEL_SIZE = fontsize
DEFAULT_LEGEND_FONT_SIZE = 6
DEFAULT_XTICK_FONT_SIZE = fontsize
DEFAULT_YTICK_FONT_SIZE = fontsize


def _sharepoint_base_path() -> Path:
    """
    Resolve SharePoint root from environment, with a project default fallback.
    """
    raw = os.environ.get(ENV_KEY, "").strip()
    if raw:
        return Path(raw)

    os.environ[ENV_KEY] = str(DEFAULT_SHAREPOINT_PATH)
    return DEFAULT_SHAREPOINT_PATH


def get_sharepoint_path() -> str:
    """
    Compatibility helper used by legacy modules.
    """
    return str(_sharepoint_base_path())


def get_path_for_folder(folder_name: str) -> str:
    """
    Return absolute path for a project folder inside the SharePoint root.
    """
    folder = Path(folder_name)
    if folder.is_absolute():
        return str(folder)
    return str(_sharepoint_base_path() / folder)



def get_sharepoint_folder(folder_name: str) -> Path:
    """
    Return absolute folder path inside the SharePoint root.
    """
    return Path(get_path_for_folder(folder_name))


def resolve_pub259_diagram_dir(
    target: FigureTarget,
    domain: FigureDomain,
    *,
    project_folder: str = PUB259_PROJECT_FOLDER,
) -> Path:
    """
    Resolve and create the diagram output directory:
    <project>/diagram/<target>/<domain>
    """
    if target not in FIGURE_TARGET_CHOICES:
        raise ValueError(f"Unsupported figure target: {target}")
    if domain not in FIGURE_DOMAIN_CHOICES:
        raise ValueError(f"Unsupported figure domain: {domain}")
    base = get_sharepoint_folder(project_folder)
    out_dir = base / "diagram" / target / domain
    out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir


def resolve_pub259_figure_stem(
    target: FigureTarget,
    domain: FigureDomain,
    stem_name: str,
    *,
    project_folder: str = PUB259_PROJECT_FOLDER,
) -> Path:
    """
    Resolve figure stem path under <project>/diagram/<target>/<domain>/.
    """
    clean_stem = Path(stem_name).with_suffix("").name
    if not clean_stem:
        raise ValueError("Figure stem name cannot be empty.")
    return resolve_pub259_diagram_dir(target, domain, project_folder=project_folder) / clean_stem


def resolve_pub259_mirrored_presentation_png_path(path: str | Path) -> Path | None:
    """
    Map a paper-target PNG path/stem to the sibling presentation PNG path.

    Expected input layout:
    <project>/diagram/paper/<domain>/...
    """
    candidate = Path(path)
    suffix = candidate.suffix.lower()
    stem_like = candidate.with_suffix("") if suffix == ".png" else candidate
    parts_lower = [part.lower() for part in stem_like.parts]

    for idx in range(len(parts_lower) - 2):
        if parts_lower[idx] == "diagram" and parts_lower[idx + 1] == "paper":
            mirrored_parts = list(stem_like.parts)
            mirrored_parts[idx + 1] = "presentation"
            mirrored_stem = Path(*mirrored_parts)
            return mirrored_stem.with_suffix(".png")
    return None
def get_google_credentials() -> str:
    """
    Compatibility helper required by helper/TGA.py.
    """
    env_path = os.getenv("H2LAB_GOOGLE_CREDENTIALS", "").strip()
    if env_path:
        return env_path
    local_path = Path(__file__).resolve().parents[1] / "helper" / "google_api_credentials.json"
    if local_path.exists():
        return str(local_path)
    raise FileNotFoundError(
        "Google credentials not found. Set H2LAB_GOOGLE_CREDENTIALS or "
        "place helper/google_api_credentials.json."
    )


def get_color_scheme(path_to_color: str):
    """
    Compatibility helper used by legacy modules.
    """
    with open(path_to_color, "r", encoding="utf-8") as file:
        color_scheme = json.load(file)
    return color_scheme


def get_pub259_plot_size_in() -> tuple[float, float]:
    """
    Return standard plot size (inches) for PUB_25_9 figures.
    Optional override via env: PUB259_PLOT_SIZE_IN='13.333,7.5'
    """
    raw = os.environ.get(PLOT_SIZE_ENV_KEY, "").strip()
    if not raw:
        return DEFAULT_PLOT_SIZE_IN
    try:
        w_raw, h_raw = [x.strip() for x in raw.split(",", 1)]
        w, h = float(w_raw), float(h_raw)
        if w > 0 and h > 0:
            return (w, h)
    except Exception:
        pass
    return DEFAULT_PLOT_SIZE_IN


def cm_to_in(cm: float) -> float:
    """
    Convert centimeters to inches.
    """
    return float(cm) / 2.54


def get_paper_figsize(mode: Literal["paper_full", "paper_half"]) -> tuple[float, float]:
    """
    Return paper figure size in inches for full/half-width profiles.
    Height is derived by fixed aspect ratio.
    """
    if mode == "paper_full":
        width_cm = PAPER_TEXT_WIDTH_CM
    elif mode == "paper_half":
        width_cm = PAPER_HALF_WIDTH_CM
    else:
        raise ValueError(f"Unsupported paper figure mode: {mode}")
    width_in = cm_to_in(width_cm)
    height_in = width_in * PAPER_ASPECT_RATIO
    return (width_in, height_in)


def get_pub259_plot_font() -> str:
    """
    Return standard plot font family for PUB_25_9 figures.
    Configure directly in setting.py via DEFAULT_PLOT_FONT.
    """
    return DEFAULT_PLOT_FONT


def apply_pub259_plot_style(
    font_size: int | None = None,
    axes_label_size: int | None = None,
    legend_font_size: int | None = None,
    xtick_font_size: int | None = None,
    ytick_font_size: int | None = None,
    figsize: tuple[float, float] | None = None,
) -> None:
    """
    Apply consistent PUB_25_9 plotting defaults (font + default figure size).
    """
    import matplotlib.pyplot as plt

    font_size = DEFAULT_FONT_SIZE if font_size is None else font_size
    axes_label_size = DEFAULT_AXES_LABEL_SIZE if axes_label_size is None else axes_label_size
    legend_font_size = DEFAULT_LEGEND_FONT_SIZE if legend_font_size is None else legend_font_size
    xtick_font_size = DEFAULT_XTICK_FONT_SIZE if xtick_font_size is None else xtick_font_size
    ytick_font_size = DEFAULT_YTICK_FONT_SIZE if ytick_font_size is None else ytick_font_size
    figsize = get_pub259_plot_size_in() if figsize is None else figsize

    plt.rcParams.update(
        {
            "font.family": get_pub259_plot_font(),
            "font.size": font_size,
            "axes.labelsize": axes_label_size,
            "legend.fontsize": legend_font_size,
            "xtick.labelsize": xtick_font_size,
            "ytick.labelsize": ytick_font_size,
            "figure.figsize": figsize,
            # Keep text editable when SVG is inserted into PowerPoint.
            "svg.fonttype": "none",
        }
    )


def configure_pub259_legend(
    ax: Axes,
    *,
    title: str | None = None,
    **legend_kwargs,
) -> Legend | None:
    """
    Create a consistent PUB_25_9 legend (frameoff + bold title).
    """
    legend_kwargs.setdefault("frameon", False)
    legend_kwargs.setdefault("ncol", 1)
    if title is not None:
        legend_kwargs.setdefault("title", title)
    legend = ax.legend(**legend_kwargs)
    if legend is None:
        return None
    title_text = legend.get_title()
    if title is not None:
        title_text.set_text(title)
    if title_text.get_text():
        title_text.set_fontweight("bold")
    return legend


def export_powerpoint_safe_svg(
    svg_path: str | Path, inkscape_path: str | Path | None = None
) -> Path | None:
    """
    Create a PowerPoint-friendly plain SVG copy next to svg_path.
    Output file suffix: *_ppt.svg
    Returns output path when created, otherwise None.
    """
    src = Path(svg_path)
    if src.suffix.lower() != ".svg" or (not src.exists()):
        return None

    inkscape_candidates = []
    if inkscape_path is not None:
        inkscape_candidates.append(str(inkscape_path))
    inkscape_candidates.extend(
        [
            shutil.which("inkscape"),
            shutil.which("inkscape.exe"),
            r"C:\Program Files\Inkscape\bin\inkscape.exe",
            r"C:\Program Files\Inkscape\inkscape.exe",
        ]
    )
    inkscape = next((c for c in inkscape_candidates if c and Path(c).exists()), None)
    if not inkscape:
        print(
            "[Warning] Inkscape not found by Python. "
            "Check PATH for the current Python/IDE process or install path."
        )
        return None

    out = src.with_name(f"{src.stem}_ppt.svg")
    commands = [
        # Inkscape >= 1.0 style
        [
            inkscape,
            str(src),
            "--export-plain-svg",
            f"--export-filename={out}",
        ],
        # Legacy fallback style
        [
            inkscape,
            f"--export-plain-svg={out}",
            str(src),
        ],
    ]
    for cmd in commands:
        try:
            subprocess.run(
                cmd,
                check=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            return out
        except subprocess.CalledProcessError as exc:
            stderr = (exc.stderr or "").strip()
            if stderr:
                print(f"[Warning] Inkscape export failed: {stderr}")
        except Exception as exc:
            print(f"[Warning] Inkscape export failed: {exc}")
    return None


def save_pub259_figure(
    fig: Figure,
    stem: str | Path,
    *,
    png_dpi: int = 300,
    svg_dpi: int | None = None,
    inkscape_path: str | Path | None = None,
) -> tuple[Path, Path | None, Path | None]:
    """
    Save a figure with the shared PUB_25_9 export algorithm.

    Outputs:
    - `<stem>.png`
    - `<stem>.svg` (presentation target only)
    - `<stem>_ppt.svg` (presentation target only; when Inkscape plain-SVG export succeeds)
    """
    out_stem = Path(stem).with_suffix("")
    png_path = out_stem.with_suffix(".png")
    svg_path = out_stem.with_suffix(".svg")
    is_paper_target = any(part.lower() == "paper" for part in out_stem.parts)

    png_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(png_path, dpi=png_dpi, bbox_inches=None)
    if is_paper_target:
        mirrored_png_path = resolve_pub259_mirrored_presentation_png_path(png_path)
        if mirrored_png_path is not None:
            mirrored_png_path.parent.mkdir(parents=True, exist_ok=True)
            fig.savefig(mirrored_png_path, dpi=png_dpi, bbox_inches=None)
        return png_path, None, None

    fig.savefig(svg_path, dpi=max(600, png_dpi) if svg_dpi is None else svg_dpi, bbox_inches=None)
    ppt_svg_path = export_powerpoint_safe_svg(svg_path, inkscape_path=inkscape_path)
    return png_path, svg_path, ppt_svg_path




