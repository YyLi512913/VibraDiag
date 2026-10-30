import csv
import hashlib
import io
import math
import numpy as np
from scipy.signal import find_peaks
from scipy.signal.windows import hann

def analyze_csv(raw: bytes, sampling_rate: float, unit: str) -> dict:
    if not math.isfinite(sampling_rate) or sampling_rate <= 0:
        raise ValueError('采样率必须是有限正数')
    if unit not in ('g', 'm/s2'):
        raise ValueError('单位只支持 g 或 m/s2')
    if len(raw) > 32 * 1024 * 1024:
        raise ValueError('文件超过 32 MiB')
    try:
        rows = csv.reader(io.StringIO(raw.decode('utf-8-sig')))
        if next(rows, None) != ['value']:
            raise ValueError('CSV 必须只有一列表头 value')
        values = []
        for line, row in enumerate(rows, 2):
            if len(row) != 1:
                raise ValueError(f'第 {line} 行须包含一个数值，不能空行')
            try:
                value = float(row[0])
            except ValueError:
                raise ValueError(f'第 {line} 行不是数值') from None
            if not math.isfinite(value):
                raise ValueError(f'第 {line} 行不是有限数值')
            values.append(value)
            if len(values) > 1_000_000:
                raise ValueError('采样点超过 1000000')
    except UnicodeDecodeError:
        raise ValueError('文件须为 UTF-8 CSV') from None
    if len(values) < 1024:
        raise ValueError('基础分析至少需要 1024 个采样点')
    x = np.asarray(values, dtype=np.float64)
    try:
        with np.errstate(over='raise', invalid='raise', divide='raise'):
            if unit == 'g':
                x = x * 9.80665
            mean = float(x.mean())
            ac = x - mean
            rms = float(np.sqrt(np.mean(x*x)))
            variance = float(np.mean(ac*ac))
            peak = float(np.max(np.abs(x)))
            features = {'mean': mean, 'rms': rms, 'ac_rms': math.sqrt(variance),
                'peak': peak, 'peak_to_peak': float(np.max(x)-np.min(x)),
                'crest_factor': peak/rms if rms else None,
                'pearson_kurtosis': float(np.mean(ac**4)/variance**2) if variance else None}
            n = len(x)
            win = hann(n, sym=False)
            amplitude = np.abs(np.fft.rfft(ac*win))/win.sum()
            amplitude[1:-1 if n % 2 == 0 else None] *= 2
            frequency = np.fft.rfftfreq(n, 1/sampling_rate)
            if not np.all(np.isfinite(amplitude)) or not np.all(np.isfinite(frequency)):
                raise FloatingPointError()
    except (FloatingPointError, OverflowError):
        raise ValueError('数值范围超出当前计算能力，请核对单位和采样率') from None
    indices, _ = find_peaks(amplitude)
    top = sorted(indices, key=lambda i: float(amplitude[i]), reverse=True)[:5]
    bins = np.array_split(np.arange(n), min(1000, n))
    preview_indices = sorted({int(i) for group in bins for i in (group[np.argmin(x[group])], group[np.argmax(x[group])])})
    warnings = []
    if variance == 0:
        warnings.append('恒定信号：峭度不可计算，不据此判定设备健康')
    if rms == 0:
        warnings.append('零信号：峰值因子不可计算')
    return {'sha256': hashlib.sha256(raw).hexdigest(), 'sample_count': n,
        'sampling_rate_hz': sampling_rate, 'input_unit': unit, 'output_unit': 'm/s2',
        'duration_seconds': n/sampling_rate, 'bin_spacing_hz': sampling_rate/n,
        'features': features, 'peaks': [{'hz':float(frequency[i]),'amplitude':float(amplitude[i])} for i in top],
        'warnings': warnings, 'algorithm_version':'basic-v1',
        'processing':{'segment':'full_file','window':'periodic_hann','demean_for_spectrum':True,'amplitude':'single_sided_peak'},
        'waveform_preview': [[i/sampling_rate,float(x[i])] for i in preview_indices],
        'spectrum': {'frequency_hz':frequency.tolist(),'amplitude_m_s2':amplitude.tolist()}}
