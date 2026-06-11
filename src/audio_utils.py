import os
import numpy as np
import soundfile as sf
import librosa

SUPPORTED_FORMATS = {".wav", ".mp3", ".flac", ".m4a", ".ogg", ".aac", ".wma"}


def is_audio_file(path):
    ext = os.path.splitext(path)[1].lower()
    return ext in SUPPORTED_FORMATS


def list_audio_files(directory):
    files = []
    for f in os.listdir(directory):
        if is_audio_file(f):
            files.append(os.path.join(directory, f))
    return sorted(files)


def load_audio(path, target_sr=16000):
    audio, sr = sf.read(path)
    if sr != target_sr:
        audio = librosa.resample(audio, orig_sr=sr, target_sr=target_sr)
        sr = target_sr
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    return audio, sr


def save_audio(path, audio, sr=16000):
    sf.write(path, audio, sr)


def record_audio(duration=5, sr=16000, device=None):
    import sounddevice as sd

    print(f"录音中... ({duration}秒)")
    audio = sd.rec(int(duration * sr), samplerate=sr, channels=1, device=device)
    sd.wait()
    print("录音完成")
    return audio.flatten(), sr


def compute_snr(audio, noise_floor=None):
    if noise_floor is None:
        noise_floor = np.percentile(np.abs(audio), 10)
    signal_power = np.mean(audio ** 2)
    noise_power = noise_floor ** 2
    if noise_power == 0:
        return float("inf")
    snr = 10 * np.log10(signal_power / noise_power)
    return round(snr, 2)
