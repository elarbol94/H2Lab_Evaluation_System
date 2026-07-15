from pathlib import Path
import os
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

from setting import get_path_for_folder
from helper.TGA import TGAExperiment
from helper.Filter import RollingAverage, ExponentialMovingAverage, SavitzkyGolayFilter, GaussianFilter, \
    MedianFilter, ButterworthFilter

# ---------------------------------------------------------
# Set working directory
# ---------------------------------------------------------
work_dir = Path(get_path_for_folder('H2Lab_PUB_25_9 Lime in EAFD Recycling'))
Path.cwd().resolve()
if Path.cwd() != work_dir:
    os.chdir(work_dir)


# ---------------------------------------------------------
# Helpers
# ---------------------------------------------------------
def derive_column(df: pd.DataFrame, col_name: str, new_name: str) -> pd.DataFrame:
    df[new_name] = df[col_name].diff() / df['time_min'].diff()
    df.at[df.index[0], new_name] = df[new_name].iloc[1]
    return df


def cut_reactive_segment(df: pd.DataFrame, cut_mode: str = 'upper') -> pd.DataFrame:
    series = df['Temperature']
    idx_start = np.where(series > 500)[0][0] + 5
    idx_end = np.where(series == series.max())[0][0]

    if cut_mode == 'upper':
        return df.iloc[:idx_end]
    elif cut_mode == 'lower':
        return df.iloc[idx_start:]
    elif cut_mode == 'both':
        return df.iloc[idx_start:idx_end]
    return df


# ---------------------------------------------------------
# Main preparation function
# ---------------------------------------------------------
def cut_dataframe(df):
    y_series = derive_column(df, 'dmdt_filtered_%min', 'dmdtdt')

    index_list = np.where(y_series['dmdtdt'].iloc[:-200]<-0.8)[0]
    if len(index_list)>0:
        index = index_list[0]
    else:
        index = len(y_series)
    return df.iloc[:index]


def prepare_experimental_data(
        path_of_file: Path, experiment_id: str,
        cut_mode: str = 'upper'
):
    file = TGAExperiment(file_path=str(path_of_file))
    file.convert_time_to_minutes()

    # --------------------------------
    # Store original dm for comparison
    # --------------------------------
    file.df.rename(columns={'dm': 'dm_original_mg'}, inplace=True)
    file.calculate_relative_mass_loss(mass_col='dm_original_mg', new_col='dm_original_%')
    # file.df['dm_original_%'] += (100 - file.df['dm_original_%'].iloc[0])

    # --------------------------------
    # Filter dm -> dm
    # --------------------------------
    filtered_mass_col = 'dm_filtered_mg'
    filtered_kinetic_col = 'dmdt_filtered_mgmin'
    original_mass_col = 'dm_original_mg'
    original_kinetic_col = 'dmdt_original_mgmin'

    pre_filter = MedianFilter(kernel_size=9)
    file.apply_filter(pre_filter, 'time_min', 'dm_original_mg', result_col=filtered_mass_col)

    # --------------------------------
    # Calculate mass % from filtered dm
    # --------------------------------
    file.calculate_relative_mass_loss(mass_col=filtered_mass_col, new_col='dm_filtered_%')
    # file.df['dm_filtered_%'] += (100 - file.df['dm_filtered_%'].iloc[0])

    # --------------------------------
    # Compute dmdt from filtered dm_filtered_%
    # also keep original kinetics from original dm
    # --------------------------------
    file.df = derive_column(file.df, 'dm_filtered_%', 'dmdt_filtered_%min')
    file.df = derive_column(file.df, 'dm_original_%', 'dmdt_original_%min')

    # --------------------------------
    # Smooth dmdt
    # --------------------------------
    # post_filter = GaussianFilter(sigma=1)
    # post_filter = SavitzkyGolayFilter(window_length=29, polyorder=2)
    post_filter = ButterworthFilter(cutoff=0.01, order=2, time_unit='min')

    file.apply_filter(post_filter, 'time_min', 'dmdt_filtered_%min', result_col='dmdt_filtered_%min')

    # --------------------------------
    # Cut reactive segment
    # --------------------------------

    df = cut_reactive_segment(file.df.copy(), cut_mode)

    df = cut_dataframe(df.copy())
    # handle gases
    df['experiment id'] = experiment_id
    df['CO'] = df.get('Gas1', 0)
    df['CO2'] = df.get('Gas2', 0)
    df['Ar'] = df.get('Purge', 0)
    for col in ['Gas1', 'Gas2', 'Water', 'Purge']:
        df.drop(columns=col, errors='ignore', inplace=True)

    return df, ['dm_original_%', 'dm_filtered_%', 'dmdt_original_%min', 'dmdt_filtered_%min']


# ---------------------------------------------------------
# Plotting
# ---------------------------------------------------------
def plot_experiment(
        df: pd.DataFrame,
        experiment_name: str,
        experiment_id: str,
        y_mass_orig: str,
        y_mass_filt: str,
        y_kin_orig: str,
        y_kin_filt: str,
        save_dir: Path,
        show_plot: bool = False
):
    fig, ax = plt.subplots(3, 1, sharex=True, figsize=(7, 10))
    ax_dm, ax_dmdt, ax_T = ax
    ax_Gas = ax_T.twinx()

    # -------------------------------
    # Mass loss plot
    # -------------------------------
    ax_dm.plot(df['time_min'], df[y_mass_orig], label='Mass % original', linewidth=0.5, color='grey', linestyle='--')
    ax_dm.plot(df['time_min'], df[y_mass_filt], label='Mass % filtered', linewidth=1.2, color='C0')
    ax_dm.set_ylabel(y_mass_filt)
    ax_dm.spines['top'].set_visible(False)
    ax_dm.spines['right'].set_visible(False)
    ax_dm.legend(frameon=False, fontsize='small')

    # -------------------------------
    # Reaction kinetics plot
    # -------------------------------
    ax_dmdt.plot(df['time_min'], df[y_kin_orig], label='dM/dt original', linewidth=0.1, color='grey', linestyle='--')
    ax_dmdt.plot(df['time_min'], df[y_kin_filt], label='dM/dt filtered', linewidth=1.2, color='C1')
    ax_dmdt.set_ylabel(y_kin_filt)
    ax_dmdt.set_xlabel('Time [min]')
    ax_dmdt.spines['top'].set_visible(False)
    ax_dmdt.spines['right'].set_visible(False)
    ax_dmdt.legend(frameon=False, fontsize='small')
    ax_dmdt.set_ylim([min(df[y_kin_filt])*1.2, 0.5])

    # -------------------------------
    # Temperature + Gas flows
    # -------------------------------
    lineT, = ax_T.plot(df['time_min'], df['Temperature'], color='black', label='Temperature')
    lineCO, = ax_Gas.plot(df['time_min'], df['CO'], linewidth=0.7, label='CO')
    lineCO2, = ax_Gas.plot(df['time_min'], df['CO2'], linewidth=0.7, label='CO2')

    ax_T.set_ylabel('Temperature [°C]')
    ax_Gas.set_ylabel('Flowrate [ml/min]')
    ax_T.set_xlabel('Time [min]')
    ax_T.spines['top'].set_visible(False)
    ax_Gas.spines['top'].set_visible(False)
    ax_T.legend([lineT, lineCO, lineCO2], ['Temperature', 'CO', 'CO2'],
                frameon=False, loc='upper left', fontsize='small')

    # -------------------------------
    # Title and Save
    # -------------------------------
    ax_dm.set_title(f'Experiment {experiment_id}')
    save_path = save_dir / f"{experiment_name.split('.')[0]}.png"
    fig.savefig(save_path, dpi=600)

    if show_plot:
        plt.show()
    else:
        plt.close(fig)


# ---------------------------------------------------------
# Main script
# ---------------------------------------------------------
def main():
    path_to_data = Path('TGA/data')
    save_dir = Path('TGA/diagram')
    save_dir.mkdir(parents=True, exist_ok=True)

    all_data = pd.DataFrame()

    experiment_files = sorted(path_to_data.glob('*_RT14*.txt'))

    for file_path in experiment_files:
        experiment_name = file_path.name
        experiment_id = experiment_name.split('_')[1].split('.')[0]
        print(f"Processing {experiment_name}")

        df, [dm_orig, dm_smooth, dmdt_orig, dmdt_smooth] = prepare_experimental_data(
            file_path, experiment_id, cut_mode='upper'
        )

        all_data = pd.concat([all_data, df], ignore_index=True)
        df.to_parquet(path_to_data / f"{experiment_id}.parquet")

        plot_experiment(
            df,
            experiment_name,
            experiment_id,
            y_mass_orig=dm_orig,
            y_mass_filt=dm_smooth,
            y_kin_orig=dmdt_orig,
            y_kin_filt=dmdt_smooth,
            save_dir=save_dir,
            show_plot=True
        )

    all_data.to_parquet(path_to_data / "allExperiments.parquet")


if __name__ == '__main__':
    main()
