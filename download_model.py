"""
Download the LLM model file from HuggingFace mirror.

Usage:
    python download_model.py

Downloads Qwen2.5-0.5B-Instruct GGUF (~469MB) to frontend/models/
Uses hf-mirror.com (HuggingFace 国内镜像).
"""
import sys
from pathlib import Path
from urllib.request import urlretrieve

MODEL_URL = "https://hf-mirror.com/Qwen/Qwen2.5-0.5B-Instruct-GGUF/resolve/main/qwen2.5-0.5b-instruct-q4_k_m.gguf"
TARGET_DIR = Path(__file__).parent / "frontend" / "models"
TARGET_FILE = TARGET_DIR / "qwen2.5-0.5b-instruct-q4_k_m.gguf"


def main():
    if TARGET_FILE.exists():
        size_mb = TARGET_FILE.stat().st_size / (1024 * 1024)
        print(f"Model already exists: {TARGET_FILE} ({size_mb:.0f} MB)")
        print("Delete it first to re-download.")
        return

    TARGET_DIR.mkdir(parents=True, exist_ok=True)
    print(f"Downloading model from {MODEL_URL}...")
    print(f"Target: {TARGET_FILE}")
    print(f"Size: ~469 MB, please wait...")

    def progress(block_num, block_size, total_size):
        downloaded = block_num * block_size
        percent = min(100, downloaded * 100 / total_size) if total_size > 0 else 0
        mb = downloaded / (1024 * 1024)
        total_mb = total_size / (1024 * 1024)
        sys.stdout.write(f"\r  {mb:.0f}/{total_mb:.0f} MB ({percent:.0f}%)")
        sys.stdout.flush()

    try:
        urlretrieve(MODEL_URL, str(TARGET_FILE), reporthook=progress)
        print("\nDone!")
    except Exception as e:
        print(f"\nDownload failed: {e}")
        print("Try manually downloading from:")
        print(f"  {MODEL_URL}")
        if TARGET_FILE.exists():
            TARGET_FILE.unlink()
        sys.exit(1)


if __name__ == "__main__":
    main()
