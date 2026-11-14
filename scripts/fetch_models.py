"""Download pinned models. Verify content before atomically installing each file."""
import hashlib
import os
from pathlib import Path
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
CV_REF = "48b10fa505aeb51fbd127fc60a5c660040e823b7"
FAS_REF = "2f8dd93dd0fd9d120f08e83d9020719f67ffdb68"
MODELS = [
    ("yunet.onnx", f"https://media.githubusercontent.com/media/opencv/opencv_zoo/{CV_REF}/models/face_detection_yunet/face_detection_yunet_2023mar.onnx", "sha256", "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4"),
    ("sface.onnx", f"https://media.githubusercontent.com/media/opencv/opencv_zoo/{CV_REF}/models/face_recognition_sface/face_recognition_sface_2021dec.onnx", "sha256", "0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79"),
    ("minifasnet.onnx", f"https://raw.githubusercontent.com/facenox/face-antispoof-onnx/{FAS_REF}/models/best/98.20/best_model.onnx", "git", "32824f13fc60018665ea1a681b6fd2d884c9609b"),
]


def digest(data, kind):
    if kind == "git":
        return hashlib.sha1(f"blob {len(data)}\0".encode() + data).hexdigest()
    return hashlib.sha256(data).hexdigest()


def main():
    folder = ROOT / "models"
    folder.mkdir(exist_ok=True)
    for name, url, kind, expected in MODELS:
        target = folder / name
        if target.exists() and digest(target.read_bytes(), kind) == expected:
            print(f"Verified {name}", flush=True)
            continue
        print(f"Downloading {name}", flush=True)
        request = Request(url, headers={"User-Agent": "HID-Hackathon-Prototype"})
        with urlopen(request, timeout=90) as response:
            data = response.read()
        if digest(data, kind) != expected:
            raise RuntimeError(f"Checksum mismatch for {name}")
        temporary = target.with_suffix(".download")
        temporary.write_bytes(data)
        os.replace(temporary, target)
        print(f"Installed {name} ({len(data):,} bytes)", flush=True)


if __name__ == "__main__":
    main()
