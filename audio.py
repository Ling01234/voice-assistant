from pydub import AudioSegment
from pydub.utils import make_chunks
import numpy as np
import io
import base64
import logging
import soundfile as sf
import time
from scipy.signal import butter, lfilter

logger = logging.getLogger("voice-assistant-app")


def preprocess_audio(audio_data, sample_rate=8000):
    """
    Pre-process the audio data:
    - Decode base64-encoded audio
    - Convert PCMU raw format to WAV
    - Normalize amplitude
    - Apply a low-pass filter for noise reduction
    """
    start_time = time.time()
    try:
        # Decode base64-encoded audio
        raw_audio = base64.b64decode(audio_data)

        # Convert PCMU (G.711 u-law) raw audio to WAV using pydub
        audio_segment = AudioSegment(
            raw_audio, frame_rate=sample_rate, sample_width=1, channels=1, codec="ulaw"
        )

        # Export audio to raw data
        raw_wave = io.BytesIO()
        audio_segment.export(raw_wave, format="wav")
        raw_wave.seek(0)

        # Load the WAV data into numpy array for processing
        audio, sr = sf.read(raw_wave, dtype="float32")

        # Normalize amplitude
        audio = audio / np.max(np.abs(audio))

        # Apply a low-pass filter to reduce high-frequency noise
        nyquist = 0.5 * sr
        cutoff = 3000  # Low-pass filter cutoff frequency in Hz
        normal_cutoff = cutoff / nyquist
        b, a = butter(6, normal_cutoff, btype="low", analog=False)
        audio = lfilter(b, a, audio)

        # Re-encode the processed audio as base64
        out_buffer = io.BytesIO()
        sf.write(out_buffer, audio, sample_rate, format="RAW", subtype="PCM_16")
        processed_audio = base64.b64encode(out_buffer.getvalue()).decode("utf-8")

        # Log success and timing
        elapsed_time = time.time() - start_time
        logger.info(f"Audio preprocessed successfully in {elapsed_time:.4f} seconds")
        return processed_audio

    except Exception as e:
        logger.error(f"Error during audio preprocessing: {e}", exc_info=True)
        return audio_data