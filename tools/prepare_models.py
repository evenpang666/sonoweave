"""Optional pre-download; missing models are also provisioned on first use."""
import argparse
from pathlib import Path
import sys
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from sonoweave.models import ensure_models


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--model', choices=('4b', '2b', 'both'), default='4b')
    parser.add_argument('--test-sample', action='store_true')
    parser.add_argument('--speaker-colors', action='store_true', help='同时准备可选的人声配色模型')
    args = parser.parse_args()
    for model in ('4b', '2b') if args.model == 'both' else (args.model,):
        ensure_models(model, progress=lambda text: print(text, flush=True), speaker_colors=args.speaker_colors)
    sample = ROOT / '.cache' / 'jfk.flac'
    if args.test_sample and not sample.exists():
        sample.parent.mkdir(parents=True, exist_ok=True)
        with urllib.request.urlopen('https://raw.githubusercontent.com/SYSTRAN/faster-whisper/master/tests/data/jfk.flac', timeout=30) as response:
            sample.write_bytes(response.read())
    print('Models ready. Recognition and translation now work offline.')


if __name__ == '__main__':
    main()
