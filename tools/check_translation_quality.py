"""Offline, reproducible semantic probes. Outputs need human review; no fake accuracy score."""
import argparse
import json
import os
from pathlib import Path
import socket
import sys
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", SONOWEAVE_HOME=str(ROOT / ".runtime"))

from sonoweave.config import translation_model_location
from sonoweave.translation import Translator
from tools.caption_metrics import distribution


CASES = (
    ("negation", "They did not say the bridge was safe.", (), "没有说桥安全；不能变成说桥不安全"),
    ("numbers", "Revenue fell by 15 percent, from 2 million to 1.7 million dollars.", (), "下降15%，200万美元到170万美元"),
    ("date", "The deadline is October 23, not October 30.", (), "截止日期10月23日，非30日"),
    ("pronoun", "She handed it to him.", ("Anna picked up the report. Tom was waiting by the door.",), "她把报告交给他，不调换主客体"),
    ("idiom", "We need to break the ice before the meeting.", (), "打破冷场，不能按砸冰理解"),
    ("exclamation", "Boy, that sounds hopeful.", ("They decided to fight poverty rather than each other.",), "Boy是感叹，不能译为男孩"),
    ("spoken_quote", "They decided to, quote, fight poverty rather than each other.", (), "quote为口头引号，不能译为互相引用或借鉴"),
    ("trade_clean", "China became India's number one trading partner in goods, at least.", (), "至少在货物贸易中是印度第一大贸易伙伴"),
    ("trade_asr_error", "China becoming India's number one source training partner in goods at least.", (), "现场ASR错误对照，原文含training；不得据此计算翻译正确率"),
    ("fragment", "And so my", (), "未完成片段，不补写谓语"),
    ("condition", "If the test fails, do not restart the server until the backup is complete.", (), "条件失败才适用；备份完成前不要重启"),
    ("scope", "Not all of the files were deleted.", (), "并非所有文件都删除了"),
    ("unit", "The dose was 0.5 milligrams, not 5 milligrams.", (), "0.5毫克，不是5毫克；仅作为翻译数字用例"),
    ("name", "OpenAI and NVIDIA announced a new project.", (), "保留OpenAI和NVIDIA，不补项目内容"),
    ("question", "You haven't sent the payment yet, have you?", (), "尚未付款的否定疑问"),
)


def no_network(*args, **kwargs):
    raise AssertionError("Runtime attempted a network connection")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--models", nargs="+", choices=("4b", "2b"), default=["4b", "2b"])
    parser.add_argument("--label", default="translation-quality")
    args = parser.parse_args()
    destination = ROOT / ".cache" / (args.label + ".json")
    reports = []
    with patch.object(socket.socket, "connect", no_network), patch.object(socket, "create_connection", no_network):
        for model in args.models:
            translator = Translator(model_path=translation_model_location(model))
            try:
                translator.warmup()
                rows = []
                for name, text, context, criterion in CASES:
                    started = time.monotonic()
                    try:
                        output = translator.translate(text, "zh-CN", "en", context=context)
                        error = None
                    except Exception as exc:
                        output, error = None, str(exc)
                    rows.append(dict(case=name, source=text, context=context, review_criterion=criterion,
                                     output=output, error=error, seconds=round(time.monotonic() - started, 4)))
                reports.append(dict(model=model, device=translator.device, gpu_layers=translator.gpu_layers,
                                    timings=distribution([row["seconds"] for row in rows]), rows=rows))
                destination.write_text(json.dumps(dict(network_blocked=True, human_review_required=True,
                    reports=reports), ensure_ascii=False, indent=2), encoding="utf-8")
                print("QUALITY", model, json.dumps(reports[-1]["timings"]), flush=True)
            finally:
                translator.close()
    print("PRIVATE_DIAGNOSTICS", destination, flush=True)


if __name__ == "__main__":
    main()
