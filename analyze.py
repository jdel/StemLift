#!/usr/bin/env python3
"""Print timestamped pitch candidates from a separated bass stem."""

import argparse
from pathlib import Path

import librosa
import numpy as np
import scipy.signal


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("audio", type=Path)
    parser.add_argument("--confidence", type=float, default=0.45)
    parser.add_argument("--minimum-duration", type=float, default=0.08)
    args = parser.parse_args()

    samples, sample_rate = librosa.load(args.audio, sr=22050, mono=True)
    bandpass = scipy.signal.butter(
        6, [38, 330], btype="bandpass", fs=sample_rate, output="sos"
    )
    bass = scipy.signal.sosfiltfilt(bandpass, samples)
    bass = librosa.effects.harmonic(bass, margin=2.5)

    hop_length = 256
    f0, voiced, probability = librosa.pyin(
        bass,
        fmin=librosa.note_to_hz("E1"),
        fmax=librosa.note_to_hz("G3"),
        sr=sample_rate,
        frame_length=4096,
        hop_length=hop_length,
        fill_na=np.nan,
    )
    times = librosa.times_like(f0, sr=sample_rate, hop_length=hop_length)
    midi = librosa.hz_to_midi(f0)
    accepted = voiced & (probability >= args.confidence) & np.isfinite(midi)
    notes = np.where(accepted, np.rint(midi), np.nan)

    # A short median filter rejects isolated octave/harmonic mistakes.
    smoothed = notes.copy()
    for index in range(2, len(notes) - 2):
        window = notes[index - 2 : index + 3]
        window = window[np.isfinite(window)]
        if len(window) >= 3:
            smoothed[index] = np.median(window)

    print("start\tduration\tnote\tmidi\tconfidence")
    start = None
    previous = None
    for index, value in enumerate(smoothed):
        note = int(value) if np.isfinite(value) else None
        if note == previous:
            continue
        if previous is not None and start is not None:
            duration = times[index] - times[start]
            if duration >= args.minimum_duration:
                confidence = float(np.nanmean(probability[start:index]))
                name = librosa.midi_to_note(previous, unicode=False)
                print(
                    f"{times[start]:.3f}\t{duration:.3f}\t{name}\t"
                    f"{previous}\t{confidence:.2f}"
                )
        start = index if note is not None else None
        previous = note

    if previous is not None and start is not None:
        duration = times[-1] - times[start]
        if duration >= args.minimum_duration:
            confidence = float(np.nanmean(probability[start:]))
            name = librosa.midi_to_note(previous, unicode=False)
            print(
                f"{times[start]:.3f}\t{duration:.3f}\t{name}\t"
                f"{previous}\t{confidence:.2f}"
            )


if __name__ == "__main__":
    main()
