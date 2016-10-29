from __future__ import print_function, division
import csv, glob, h5py, os, time

import numpy as np
import scipy as sp

from ..signal_processing import resample
from ..utils import bands, HTK, utils
from ..utils.electrodes import load_bad_electrodes
import transcripts

__author__ = 'David Conant, Jesse Livezey'

srf = HTK.SAMPLING_RATE_FACTOR


def run_extract_windows(blockpath, event_times, align_window, data_type, zscore_mode='events',
                        all_event_times=None, fband=None):
    """
    Extract requested data type.

    Parameters
    ----------
    blockpath : str
        Path to block.
    event_times : list of float
        Event alignment times.
    align_window : ndarray
        Window around event alignment.
    data_type : str
        Data type (e.g. 'HG', 'form').
    zscore_mode : str
        Method for zscoring data.
    """

    def neuro():
        bad_electrodes = load_bad_electrodes(blockpath)
        bad_times = load_bad_times(blockpath)
        all_bands, fs = load_neuro(blockpath)
        all_bands = all_bands[..., :256, :]

        neuro_bands = bands.neuro['bands']
        min_freqs = bands.neuro['min_freqs']
        max_freqs = bands.neuro['max_freqs']
        HG_freq = bands.neuro['HG_freq']

        D = dict()
        final_fs = dict()
        for b, minf, maxf, d in zip(neuro_bands, min_freqs, max_freqs, all_bands):
            #target_fs = HG_freq * (maxf + minf) / (max_freqs[-1] + min_freqs[-1])
            target_fs = 2. * maxf
            if np.allclose(target_fs, fs):
                X = d
            else:
                assert target_fs < fs
                X = resample.resample_ecog(d, target_fs, fs)
            X = zscore(X, sampling_freq=target_fs, bad_times=bad_times,
                          align_window=align_window, mode=zscore_mode,
                          all_event_times=all_event_times)
            D[b] = extract_windows(X, target_fs, event_times, align_window,
                                   bad_times=bad_times, bad_electrodes=bad_electrodes)
            final_fs[b] = target_fs

        return neuro_bands, D, final_fs

    def HG():
        hg_bands = ['high gamma']
        bad_electrodes = load_bad_electrodes(blockpath)
        bad_times = load_bad_times(blockpath)
        hg, fs = load_HG(blockpath)

        hg = hg[..., :256, :]

        hg = zscore(hg, sampling_freq=fs, bad_times=bad_times,
                    align_window=align_window, mode=zscore_mode,
                    all_event_times=all_event_times)

        D = extract_windows(hg, fs, event_times, align_window,
                  bad_times=bad_times, bad_electrodes=bad_electrodes)

        return hg_bands, {hg_bands[0]: D}, {hg_bands[0]: fs}

    def AS():
        bad_electrodes = load_bad_electrodes(blockpath)
        bad_times = load_bad_times(blockpath)

        min_freqs = bands.neuro['min_freqs']
        max_freqs = bands.neuro['max_freqs']
        HG_freq = bands.neuro['HG_freq']

        chang_lab_cfs = bands.chang_lab['cfs']

        D = dict()
        final_fs = dict()

        target_fs = HG_freq * 2. * chang_lab_cfs[fband] / (max_freqs[-1] + min_freqs[-1])
        start = time.time()
        d, fs = load_AS(blockpath, fband, target_fs)
        print('load', blockpath, fband, target_fs, time.time()-start)
        start = time.time()
        X = abs(d)
        print('abs', blockpath, fband, target_fs, time.time()-start)
        start = time.time()
        X = zscore(X, sampling_freq=target_fs, bad_times=bad_times,
                      align_window=align_window, mode=zscore_mode,
                      all_event_times=all_event_times)
        print('zscore', blockpath, fband, target_fs, time.time()-start)
        D[fband] = extract_windows(X, target_fs, event_times, align_window,
                                   bad_times=bad_times, bad_electrodes=bad_electrodes)
        final_fs[fband] = target_fs
        return [fband], D, final_fs

    def narrow_neuro():
        bad_electrodes = load_bad_electrodes(blockpath)
        bad_times = load_bad_times(blockpath)

        neuro_bands = bands.neuro['bands']
        min_freqs = bands.neuro['min_freqs']
        max_freqs = bands.neuro['max_freqs']
        HG_freq = bands.neuro['HG_freq']

        chang_lab_cfs = bands.chang_lab['cfs']

        D = dict()
        final_fs = dict()
        for b, minf, maxf in zip(neuro_bands, min_freqs, max_freqs):
            peakf = (minf + maxf) / 2.
            close_idx = np.argmin(abs(chang_lab_cfs - peakf))
            #target_fs = 2. *  chang_lab_cfs[close_idx]
            target_fs = HG_freq * 2. * chang_lab_cfs[close_idx] / (max_freqs[-1] + min_freqs[-1])
            print(blockpath, b, target_fs)
            d, fs = load_AS(blockpath, close_idx, target_fs)
            X = abs(d)
            X = zscore(X, sampling_freq=target_fs, bad_times=bad_times,
                          align_window=align_window, mode=zscore_mode,
                          all_event_times=all_event_times)
            D[b] = extract_windows(X, target_fs, event_times, align_window,
                                   bad_times=bad_times, bad_electrodes=bad_electrodes)
            final_fs[b] = target_fs

        return neuro_bands, D, final_fs

    options = {'HG' : HG,
               'AS' : AS,
               'neuro': neuro,
               'narrow_neuro': narrow_neuro}

    return options[data_type]()


def zscore(data, axis=-1, mode=None, sampling_freq=None, bad_times=None,
           align_window=None, all_event_times=None):

    if mode is None:
        mode = 'events'

    if mode == 'whole':
        tmp = data
    elif mode == 'between_data':
        tt_data = np.arange(data.shape[axis]) / sampling_freq
        data_start = all_event_times.min() + align_window[0]
        data_stop = all_event_times.max() + align_window[1]
        data_time = utils.is_in(tt_data, np.array([data_start, data_stop]))
        for bt in bad_times:
            data_time = data_time & ~utils.is_in(tt_data, bt)
        for et in all_event_times:
            data_time = data_time & ~utils.is_in(tt_data, et + align_window)
        baseline = data[..., data_time]
    elif mode == 'data':
        tt_data = np.arange(data.shape[-1]) / sampling_freq
        data_start = all_event_times.min() + align_window[0]
        data_stop = all_event_times.max() + align_window[1]
        data_time = utils.is_in(tt_data, np.array([data_start, data_stop]))
        for bt in bad_times:
            data_time = data_time & ~utils.is_in(tt_data, bt)
        baseline = data[..., data_time]
    elif mode == 'events':
        tt_data = np.arange(data.shape[-1]) / sampling_freq
        data_time = np.zeros_like(tt_data, dtype=bool)
        for et in all_event_times:
            data_time = data_time | utils.is_in(tt_data, et + align_window)
        for bt in bad_times:
            data_time = data_time & ~utils.is_in(tt_data, bt)
        baseline = data[..., data_time]
    elif ((mode is None) or (mode.lower() == 'none')):
        return data
    else:
        raise ValueError('zscore_mode type {} not recognized.'.format(mode))


    means = baseline.mean(axis=axis, keepdims=True)
    stds = baseline.std(axis=axis, keepdims=True)
    data  = (data - means) / stds

    return data


def extract_windows(data, samping_freq, event_times, align_window=None,
                    bad_times=None, bad_electrodes=None):
    """
    Extracts windows of aligned data. Assumes constant sampling frequency.
    Assumes last two dimensions of data are electrodes and time.

    Parameters
    ----------
    data : ndarray (n_channels, n_time)
        Timeseries data.
    samping_freq : float
        Sampling frequency of data.
    event_time : list of floats
        Time (in seconds) of events.
    align_window : ndarray
        Window around event alignment.
    bad_times : ndarray (n_windows, 2)
        Start and stop points of bad times segments.
    bad_electrodes : ndarray (n_electrodes,)
        Indices of bad electrodes.

    Returns
    -------
    D : ndarray (n_events, *n_bands*, n_elects, n_time)
        Event data aligned to event times.
    """
    assert data.ndim == 2, 'Data dim is wrong'

    if align_window is None:
        align_window = np.array([-1., 1.])
    else:
        align_window = np.array(align_window)
        assert align_window[0] <= 0., 'align window start'
        assert align_window[1] >= 0., 'align window end'
        assert align_window[0] < align_window[1], 'align window order'

    if bad_times is None:
        bad_times = np.array([])
    else:
        bad_times = np.array(bad_times)
    if bad_electrodes is None:
        bad_electrodes = np.array([])
    else:
        bad_electrodes = np.array(bad_electrodes)

    window_length = int(np.ceil(np.diff(align_window) * samping_freq))
    window_start = int(np.floor(align_window[0] * samping_freq))
    D = utils.nans((len(event_times),) + data.shape[:-1] + (window_length,), dtype=np.complex)

    def time_idx(time):
        return int(np.around(time * samping_freq))

    for ii, time in enumerate(event_times):
        sl = slice(time_idx(time) + window_start, time_idx(time) + window_start + window_length)
        event_data = data[..., sl]
        if event_data.shape == D.shape[1:]:
            D[ii] = event_data
        else:
            print('Event shape mismatch {}, {}'.format(event_data.shape, D.shape))

    if bad_times.size:
        bad_trials = []
        for ii, time in enumerate(event_times):
            if np.any(utils.is_overlap(align_window + time,
                                       bad_times)):
                bad_trials.append(ii)
        if len(bad_trials) > 0:
            D[bad_trials] = np.nan

    if bad_electrodes.size:
        bad_electrodes = bad_electrodes[bad_electrodes < D.shape[-1]]
        D[:, bad_electrodes, :] = np.nan

    return D

def load_AS(blockpath, fband, target_fs):
    """
    Reads in HTK data.

    Parameters
    ----------
    blockpath : str
        Path to block.

    Returns
    -------
    hg : ndarray (n_channels, n_time)
        High gamma data.
    fs : float
        Sampling frequency of data.
    """

    real_path = os.path.join(blockpath, 'HilbReal_4to200_40band_{}.h5'.format(fband))
    imag_path = os.path.join(blockpath, 'HilbImag_4to200_40band_{}.h5'.format(fband))

    folder = os.path.join(blockpath, 'Hilb_ds')
    tmp_path = os.path.join(folder, 'HilbComplex_4to200_40band_{}_{}_tmp.h5'.format(fband, target_fs))
    final_path = os.path.join(folder, 'HilbComplex_4to200_40band_{}_{}.h5'.format(fband, target_fs))

    try:
        with h5py.File(final_path, 'r') as f:
            X = f['data'].value
            fs = f['sampling_rate'].value
        assert np.allclose(target_fs, fs)
    except IOError:
        with  h5py.File(real_path,'r') as f:
            real = f['data'].value
            fs_r = f['sampling_rate'][0]

        with  h5py.File(imag_path,'r') as f:
            imag = f['data'].value
            fs_i = f['sampling_rate'][0]

        assert fs_r == fs_i
        fs = fs_r
        print('original fs', fs)

        X = real + 1j * imag

        if not np.allclose(target_fs, fs):
            assert target_fs < fs
            X = resample.resample_ecog(X, target_fs, fs)

            try:
                os.mkdir(folder)
            except OSError:
                pass
            with  h5py.File(tmp_path, 'w') as f:
                f.create_dataset('data', data=X)
                f.create_dataset('sampling_rate', data=np.array(target_fs))
            os.rename(tmp_path, final_path)

    return X, fs


def load_HG(blockpath):
    """
    Reads in HTK data.

    Parameters
    ----------
    blockpath : str
        Path to block.

    Returns
    -------
    hg : ndarray (n_channels, n_time)
        High gamma data.
    fs : float
        Sampling frequency of data.
    """

    htk_path = os.path.join(blockpath, 'HilbAA_70to150_8band')
    HTKout = HTK.readHTKs(htk_path)
    hg = HTKout['data']
    # Frequency in Hz
    fs = HTKout['sampling_rate']/srf

    return hg, fs

def load_neuro(block_path):
    """
    Reads in neuro band data from
    hdf5 file.

    Parameters
    ----------
    blockpath : str
        Path to block.

    Returns
    -------
    X : ndarray (n_bands, n_channels, n_time)
        Neural data
    fs : float
        Sampling frequency of data.
    """
    subject_path, block = os.path.split(block_path)
    neuro_path = os.path.join(block_path, bands.neuro['block_path'].format(block))
    with h5py.File(neuro_path, 'r') as f:
        X = f['X'].value
        fs = f.attrs['sampling_rate']

    return X, fs


def loadForm(blockpath):
    fname = glob.glob(os.path.join(blockpath + 'Analog', '*.ifc_out.txt'))
    F = []
    with open(fname[0]) as tsv:
        for column in zip(*[line for line in csv.reader(tsv, dialect="excel-tab")]):
            F.append(column)
    F = np.array(F)
    return F


def load_anatomy(subj_dir):

    anatomy_filename = glob.glob(os.path.join(subj_dir, 'anatomy', '*_anatomy.mat'))[0]
    elect_labels_filename = glob.glob(os.path.join(subj_dir, 'elec_labels.mat'))

    electrode_labels = dict()
    if anatomy_filename:
        try:
            anatomy = sp.io.loadmat(anatomy_filename)['anatomy']
            names = anatomy.dtype.names
            for n, labels in zip(names, anatomy[0][0]):
                electrode_labels[n] = np.array(labels).ravel()
        except ValueError:
            with h5py.File(anatomy_filename) as f:
                for n in f['anatomy'].keys():
                    electrode_labels[n] = f['anatomy'][n].value
    elif elect_labels_filename:
        raise NotImplementedError
        a = sp.io.loadmat(os.path.join(subj_dir, 'elec_labels.mat'))
        electrode_labels = np.array([ elem[0] for elem in a['labels'][0]])
    else:
        raise ValueError('Could not find anatomy file.')

    return electrode_labels


def load_bad_times(blockpath):
    """
    Load bad time segments.

    Parameters
    ----------
    blockpath : str
        Path to block to load bad electrodes from.

    Returns
    -------
    bad_times : ndarray
        Pairs of start and stop times for bad segments.
    """

    bad_times = []
    try:
        lab_time_conversion = transcripts.lab_time_conversion
        with open(os.path.join(blockpath, 'Artifacts', 'bad_time_segments.lab'),'rt') as f:
            lines = f.readlines()
            for line in lines:
                if 'e' in line:
                    start, stop = line.split(' ')[:2]
                    bad_times.append([float(start)/lab_time_conversion, float(stop)/lab_time_conversion])
    except IOError:
        return np.array([])

    bad_times = np.array(bad_times)

    return bad_times
