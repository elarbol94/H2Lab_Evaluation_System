import numpy as np
import pandas as pd
from statsmodels.nonparametric.smoothers_lowess import lowess
from statsmodels.tsa.holtwinters import SimpleExpSmoothing, Holt
from scipy.interpolate import interp1d
from scipy.signal import savgol_filter
from scipy.ndimage import gaussian_filter1d
from scipy.signal import medfilt
from scipy.signal import butter, filtfilt



class Filter:

    def __init__(self):
        """
        Abstract Class for Filter classes
        """
        pass


    def __call__(self, x_old, y_old):
        """
        Includes the filtering algorithm

        :rtype: pd.Series of filtered y values
        :param x_old:
        :param y_old:
        """
        raise NotImplementedError

    def __str__(self, *args, **kwargs):
        """
        Create a name of the filter including the parameters to be used for e.g. legend entries
        :param args:
        :param kwargs:
        """
        raise NotImplementedError



class ButterworthFilter(Filter):
    def __init__(self, cutoff=0.05, order=2, time_unit='min'):
        """
        Butterworth low-pass filter.

        Parameters:
            cutoff (float): Cutoff frequency in Hz (cycles per second).
            order (int): Filter order.
            time_unit (str): Unit of the time index ('sec', 'min', 'ms', etc.).
        """
        super().__init__()
        self.cutoff = cutoff
        self.order = order
        self.time_unit = time_unit

    def estimate_sampling_frequency(self, x_series):
        """
        Estimate sampling frequency from time index series.

        Parameters:
            x_series (pd.Series): Time data.

        Returns:
            float: Sampling frequency in Hz.
        """
        dt = x_series.diff().dropna().median()
        unit_scale = {
            's': 1.0,
            'sec': 1.0,
            'min': 1 / 60.0,
            'ms': 1000.0,
        }
        scale = unit_scale.get(self.time_unit, 1.0)
        fs = 1.0 / (dt / scale)
        return fs

    def __call__(self, x_old, y_old):
        """
        Apply Butterworth filter to y_old based on x_old timing.

        Parameters:
            x_old (pd.Series): Time or index values.
            y_old (pd.Series): Data series.

        Returns:
            pd.Series: Filtered signal, interpolated to x_old.
        """
        # Estimate sampling frequency
        fs = self.estimate_sampling_frequency(x_old)

        # Compute normalized cutoff (Nyquist)
        nyq = 0.5 * fs
        normal_cutoff = self.cutoff / nyq

        if not 0 < normal_cutoff < 1:
            raise ValueError(f"Normalized cutoff {normal_cutoff:.4f} must be between 0 and 1.")

        # Apply Butterworth filter
        b, a = butter(self.order, normal_cutoff, btype='low', analog=False)
        y_filtered = filtfilt(b, a, y_old)

        # Interpolate back to x_old
        unique_x = np.unique(x_old)
        f = interp1d(unique_x, y_filtered[:len(unique_x)], kind='linear', fill_value='extrapolate')
        interpolated_values = f(x_old)

        return pd.Series(interpolated_values, index=x_old).reset_index(drop=True)

    def __str__(self):
        return f"Butter_cutoff{self.cutoff}_order{self.order}"


class ExponentialMovingAverage(Filter):
    def __init__(self, alpha=0.3):
        """
        Exponential Moving Average filter.

        Parameters:
            alpha (float): Smoothing factor (0 < alpha < 1).
        """
        super().__init__()
        if not (0 < alpha <= 1):
            raise ValueError("alpha must be in the range (0, 1].")
        self.alpha = alpha

    def __call__(self, x_old, y_old):
        """
        Apply Exponential Moving Average to y_old.

        Parameters:
            x_old (pd.Series): The numerical index series.
            y_old (pd.Series): The data series.

        Returns:
            pd.Series: A series of the EMA values aligned to x_old.
        """
        ema = y_old.copy()
        for i in range(1, len(y_old)):
            ema.iloc[i] = self.alpha * y_old.iloc[i] + (1 - self.alpha) * ema.iloc[i - 1]

        return pd.Series(ema.values, index=x_old).reset_index(drop=True)

    def __str__(self):
        return f"EMA_{self.alpha}"


class MedianFilter(Filter):
    def __init__(self, kernel_size=5):
        """
        Median filter for noise reduction.

        Parameters:
            kernel_size (int): Size of the median filter window (must be odd).
        """
        super().__init__()
        if kernel_size % 2 == 0:
            raise ValueError("kernel_size must be odd.")
        self.kernel_size = kernel_size

    def __call__(self, x_old, y_old):
        """
        Apply median filter and interpolate back to original x_old.

        Parameters:
            x_old (pd.Series): The numerical index series.
            y_old (pd.Series): The data series.

        Returns:
            pd.Series: A series of filtered values aligned to x_old.
        """
        # Apply the median filter
        y_filtered = medfilt(y_old.values, kernel_size=self.kernel_size)

        # Interpolate to original x_old values
        unique_x = np.unique(x_old)
        f = interp1d(unique_x, y_filtered[:len(unique_x)],
                     kind='linear', fill_value='extrapolate')
        interpolated_values = f(x_old)

        return pd.Series(interpolated_values, index=x_old).reset_index(drop=True)

    def __str__(self):
        return f"Median_{self.kernel_size}"

class GaussianFilter(Filter):
    def __init__(self, sigma=1.0):
        """
        Gaussian filter for smoothing data.

        Parameters:
            sigma (float): Standard deviation for Gaussian kernel.
        """
        super().__init__()
        self.sigma = sigma

    def __call__(self, x_old, y_old):
        """
        Apply Gaussian smoothing and interpolate back to original x_old.

        Parameters:
            x_old (pd.Series): The numerical index series.
            y_old (pd.Series): The data series.

        Returns:
            pd.Series: A series of the smoothed values, interpolated to x_old.
        """
        # Apply Gaussian smoothing
        y_smoothed = gaussian_filter1d(y_old.values, sigma=self.sigma)

        # Interpolate back to original x_old
        unique_x = np.unique(x_old)
        f = interp1d(unique_x, y_smoothed[:len(unique_x)], kind='linear', fill_value='extrapolate')
        interpolated_values = f(x_old)

        return pd.Series(interpolated_values, index=x_old).reset_index(drop=True)

    def __str__(self):
        return f"Gaussian_{self.sigma}"


class SavitzkyGolayFilter(Filter):
    def __init__(self, window_length=5, polyorder=2):
        """
        Savitzky-Golay filter for smoothing data.

        Parameters:
            window_length (int): Length of the filter window (must be odd and >= polyorder+2).
            polyorder (int): Degree of the polynomial used to fit the samples.
        """
        super().__init__()
        self.window_length = window_length
        self.polyorder = polyorder

    def __call__(self, x_old, y_old):
        """
        Apply Savitzky-Golay filter and interpolate back to original x_old.

        Parameters:
            x_old (pd.Series): The numerical index series.
            y_old (pd.Series): The data series.

        Returns:
            pd.Series: A series of the filtered values, interpolated to x_old.
        """
        if self.window_length >= len(y_old):
            raise ValueError("window_length must be less than the length of the input series.")
        if self.window_length % 2 == 0:
            raise ValueError("window_length must be odd.")

        # Apply the Savitzky-Golay filter
        y_filtered = savgol_filter(y_old.values, self.window_length, self.polyorder, mode='interp')

        # Interpolate the result to the original x_old indices
        unique_x = np.unique(x_old)
        f = interp1d(unique_x, y_filtered[:len(unique_x)], kind='linear', fill_value='extrapolate')
        interpolated_values = f(x_old)

        return pd.Series(interpolated_values, index=x_old).reset_index(drop=True)

    def __str__(self):
        return f"SavGol_{self.window_length}_{self.polyorder}"

class RollingAverage(Filter):
    def __init__(self, sampling_rate=10):
        super().__init__()
        self.sampling_rate = sampling_rate

    def __call__(self, x_old, y_old):
        """
        Computes a rolling average of the series `y_old` indexed by `x_old`, then interpolates
        to match the original `x_old` points.

        Parameters:
            x_old (pd.Series): The numerical index series.
            y_old (pd.Series): The data series.
            window_size (int): The number of points to include in each rolling window.

        Returns:
            pd.Series: A series of the interpolated rolling average data, matched to the original x_old indices.
        """
        # Create a DataFrame from x_old and y_old
        df_rolling = pd.DataFrame({'y': y_old.values}, index=x_old)

        # Calculate the rolling average
        rolling_avg = df_rolling['y'].rolling(window=self.sampling_rate, min_periods=1, center=True).mean()

        # Fill NaN values which might be present at the beginning or the end
        rolling_avg = rolling_avg.bfill().ffill()

        # Setup the interpolation function using unique x_old values to avoid duplicates issues
        unique_x = np.unique(df_rolling.index)
        unique_rolling_avg = rolling_avg.loc[unique_x]

        f = interp1d(unique_x, unique_rolling_avg,
                     fill_value="extrapolate", kind='linear')

        # Interpolate to the original x_old indices
        interpolated_values = f(x_old)

        # Return as a pandas Series
        return pd.Series(interpolated_values, index=x_old).reset_index(drop=True)

    def __str__(self):
        return f"RolAv_{self.sampling_rate}"


    def calc_rolling_average(self, y_old):
        """
        :type y_old: pd.Series
        """

        i = 1
        y_average = []
        y_sum = 0
        for j, y in enumerate(y_old):

            y_sum += y

            if i == self.sampling_rate:
                y_mean = y_sum / self.sampling_rate
                i = 0
                y_sum = 0
                y_average.append(y_mean)

            if j == len(y_old) - 1 and len(y_old) % self.sampling_rate > 0:
                devide = len(y_old) % self.sampling_rate
                y_mean = y_sum / devide
                y_average.extend([y_mean, y_mean])

            i += 1

        return y_average


class MovingAverageDecimator:
    def __init__(self, sampling_rate=10, step=None, align='center', include_partial=False):
        self.sampling_rate = int(sampling_rate)
        self.step = int(step) if step is not None else self.sampling_rate
        self.align = align
        self.include_partial = include_partial

    def _place_x(self, xs: pd.Series):
        if self.align in ('center', 'mean'):
            return float(xs.mean())
        elif self.align == 'median':
            return float(xs.median())
        elif self.align == 'left':
            return xs.iloc[0]
        elif self.align == 'right':
            return xs.iloc[-1]
        else:
            raise ValueError(f"Invalid align value: {self.align}")

    def __call__(self, df: pd.DataFrame, x_col: str) -> pd.DataFrame:
        df = df.reset_index(drop=True)
        n = len(df)
        w = self.sampling_rate
        s = self.step

        rows = []
        start = 0
        while start + w <= n:
            chunk = df.iloc[start:start + w]
            avg_row = chunk.mean(numeric_only=True)
            avg_row[x_col] = self._place_x(chunk[x_col])
            rows.append(avg_row)
            start += s

        if self.include_partial and start < n:
            chunk = df.iloc[start:n]
            avg_row = chunk.mean(numeric_only=True)
            avg_row[x_col] = self._place_x(chunk[x_col])
            rows.append(avg_row)

        result = pd.DataFrame(rows)

        # Shift time so it starts at 0
        if not result.empty:
            result[x_col] = result[x_col] - result[x_col].iloc[0]

        return result
