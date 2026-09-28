"""Script beats -> one Piper wav per beat, under <workspace>/assets/narration/.

Files are named by a hash of voice + text, so re-planning after a script edit only
re-synthesises the beats whose text changed.
"""

from __future__ import annotations

import hashlib
import os
import wave
from dataclasses import dataclass
from pathlib import Path

from .script import Script

PROJECT_ROOT = Path(__file__).resolve().parents[2]
VOICES_DIR = Path(os.environ.get("MONTAGE_VOICES_DIR", PROJECT_ROOT / "voices"))


class NarrationError(RuntimeError):
    pass


@dataclass(frozen=True)
class Narration:
    src: str  # workspace-relative, for the scene plan
    path: Path
    seconds: float


def wav_seconds(path: Path) -> float:
    with wave.open(str(path), "rb") as w:
        return w.getnframes() / w.getframerate()


def _load_voice(voice: str):
    model = VOICES_DIR / f"{voice}.onnx"
    if not model.is_file():
        raise NarrationError(
            f"Piper voice '{voice}' not found in {VOICES_DIR}. Download it with:\n"
            f"  .venv/Scripts/python.exe -m piper.download_voices --download-dir voices {voice}")
    try:
        from piper import PiperVoice
    except ImportError as exc:
        raise NarrationError("piper-tts not installed in this environment (use the project .venv)") from exc
    return PiperVoice.load(model)


def narrate(script: Script, voice: str, workspace: Path) -> list[Narration]:
    out_dir = workspace / "assets" / "narration"
    out_dir.mkdir(parents=True, exist_ok=True)
    loaded = None
    result = []
    for i, beat in enumerate(script.beats, 1):
        digest = hashlib.sha1(f"{voice}\n{beat.narration}".encode()).hexdigest()[:10]
        path = out_dir / f"beat_{i:02d}_{digest}.wav"
        if not path.is_file():
            if loaded is None:
                loaded = _load_voice(voice)
            tmp = path.with_suffix(".tmp")
            with wave.open(str(tmp), "wb") as wf:
                loaded.synthesize_wav(beat.narration, wf)
            tmp.replace(path)
        seconds = wav_seconds(path)
        if seconds <= 0:
            raise NarrationError(f"beat {i}: Piper produced empty audio for {beat.narration!r}")
        result.append(Narration(f"./assets/narration/{path.name}", path, seconds))
    return result
