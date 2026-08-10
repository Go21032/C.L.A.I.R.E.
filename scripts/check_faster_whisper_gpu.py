"""
check_faster_whisper_gpu.py
------------------------------
faster-whisper(土台はCTranslate2)が、本機のGPU(RTX 5070 Ti = sm_120, Blackwell)で
動くかどうかを切り分けるための検証スクリプト。9日目ノート
(サポートAI作製計画/9日目自前音声UIとストリーミング音声対話.md)②の部品。

狙い:
  9日目ノート②で「STT方式ごと変わりうる最優先リスク」と位置づけている検証を、
  1回叩けば再現できる形にし、かつ**結果を毎回ファイルに保存する**
  (ノート作成規則②:「標準出力のみで結果を残さなかった」事故を繰り返さないため)。

  単に成功/失敗を見るだけでなく、失敗した場合に
  「CTranslate2がsm_120を認識していないのか」「CUDA/cuDNNの不足なのか」
  「faster-whisper自体が入っていないのか」を切り分けられるよう、
  各レイヤーを個別に検証してから統合結果を出す。

使い方:
    python check_faster_whisper_gpu.py
    python check_faster_whisper_gpu.py --model small --compute-type int8_float16
    python check_faster_whisper_gpu.py --skip-cpu-fallback   # GPU検証のみ行う(速い)

出力先: scripts/results/gpu_check/gpu_check_<日時>.md (表形式で保存する。標準出力にも同じ内容を出す)

標準ライブラリ + faster-whisper/ctranslate2(検証対象そのもの)のみを使う。
"""

from __future__ import annotations

import argparse
import platform
import subprocess
import sys
import traceback
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_OUTPUT_DIR = SCRIPT_DIR / "results" / "gpu_check"

DEFAULT_MODEL = "small"
DEFAULT_COMPUTE_TYPE = "int8_float16"


@dataclass
class LayerCheck:
    """1つの検証レイヤーの結果(成功/失敗・詳細メッセージ)。"""

    name: str
    ok: bool
    detail: str
    error_traceback: str | None = None


@dataclass
class CheckReport:
    layers: list[LayerCheck] = field(default_factory=list)
    gpu_load_ok: bool = False
    cpu_load_ok: bool | None = None  # Noneなら未検証(--skip-cpu-fallback)
    conclusion: str = ""
    recommendation: str = ""


def _run(cmd: list[str]) -> tuple[int, str]:
    """サブプロセスを実行し、(returncode, 標準出力+標準エラー) を返す。"""
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
        return proc.returncode, (proc.stdout + proc.stderr).strip()
    except FileNotFoundError:
        return -1, f"コマンドが見つかりません: {cmd[0]}"
    except subprocess.TimeoutExpired:
        return -1, "タイムアウト"


def check_nvidia_smi(report: CheckReport) -> None:
    """レイヤー0: そもそもGPUドライバがGPUを認識しているか(faster-whisper以前の切り分け)。"""
    code, output = _run(["nvidia-smi", "--query-gpu=name,driver_version,memory.total", "--format=csv,noheader"])
    if code == 0 and output:
        report.layers.append(LayerCheck("nvidia-smi(ドライバがGPUを認識しているか)", True, output))
    else:
        report.layers.append(
            LayerCheck(
                "nvidia-smi(ドライバがGPUを認識しているか)",
                False,
                f"nvidia-smiの実行に失敗、または出力なし: {output}",
            )
        )


def check_pytorch_cuda(report: CheckReport) -> None:
    """レイヤー1(参考情報): PyTorch側からCUDAが見えているか。入っていなければスキップして良い
    (faster-whisperはPyTorch非依存だが、同じ環境でPyTorchのCUDA検出に失敗する場合、
    ドライバ/CUDA Toolkitのインストール自体を疑う材料になる)。"""
    try:
        import torch  # type: ignore

        available = torch.cuda.is_available()
        detail = f"torch={torch.__version__}, cuda.is_available()={available}"
        if available:
            detail += f", device_name={torch.cuda.get_device_name(0)}"
        report.layers.append(LayerCheck("PyTorchからのCUDA検出(参考情報)", available, detail))
    except ImportError:
        report.layers.append(LayerCheck("PyTorchからのCUDA検出(参考情報)", True, "PyTorch未インストール(このチェックはスキップ扱い。必須ではない)"))
    except Exception as e:  # noqa: BLE001 - 検証スクリプトなので広く捕捉して記録する
        report.layers.append(LayerCheck("PyTorchからのCUDA検出(参考情報)", False, f"{type(e).__name__}: {e}"))


def check_ctranslate2(report: CheckReport) -> None:
    """レイヤー2: CTranslate2自体がCUDAデバイスを認識しているか(faster-whisperの直接の土台)。"""
    try:
        import ctranslate2  # type: ignore

        version = ctranslate2.__version__
        cuda_count = ctranslate2.get_cuda_device_count()
        ok = cuda_count > 0
        detail = f"ctranslate2=={version}, get_cuda_device_count()={cuda_count}"
        report.layers.append(LayerCheck("CTranslate2のCUDAデバイス検出", ok, detail))
    except ImportError as e:
        report.layers.append(
            LayerCheck("CTranslate2のCUDAデバイス検出", False, f"ctranslate2が未インストール: {e}")
        )
    except Exception as e:  # noqa: BLE001
        report.layers.append(
            LayerCheck(
                "CTranslate2のCUDAデバイス検出",
                False,
                f"{type(e).__name__}: {e}",
                error_traceback=traceback.format_exc(),
            )
        )


def check_faster_whisper_load(
    report: CheckReport, model_size: str, compute_type: str, device: str
) -> LayerCheck:
    """レイヤー3(本命): 実際にWhisperModelを指定デバイスでロードできるかを試す。
    ロード成功後、極小の無音データで1回推論を試すところまで確認する
    (ロードだけ通ってforward時に落ちるケースがあるため)。"""
    label = f"WhisperModel({model_size}, device={device}, compute_type={compute_type})のロード"
    try:
        from faster_whisper import WhisperModel  # type: ignore
        import numpy as np  # faster-whisperの依存に含まれる

        model = WhisperModel(model_size, device=device, compute_type=compute_type)

        # 1秒分の無音(16kHz, float32)で実際に推論経路まで通す。
        silence = np.zeros(16000, dtype=np.float32)
        segments, info = model.transcribe(silence, language="ja")
        list(segments)  # ジェネレータを消費して例外が出るか確認する

        detail = f"ロード成功。推論経路も通過(検出言語={info.language}, 言語確度={info.language_probability:.2f})"
        return LayerCheck(label, True, detail)
    except ImportError as e:
        return LayerCheck(label, False, f"faster-whisperが未インストール: {e}")
    except Exception as e:  # noqa: BLE001 - 失敗理由をそのままノートに残すため広く捕捉
        return LayerCheck(label, False, f"{type(e).__name__}: {e}", error_traceback=traceback.format_exc())


def run_all_checks(
    model_size: str, compute_type: str, skip_cpu_fallback: bool
) -> CheckReport:
    report = CheckReport()

    print("[1/4] nvidia-smiでGPU認識を確認中...")
    check_nvidia_smi(report)

    print("[2/4] PyTorchからのCUDA検出を確認中(参考情報)...")
    check_pytorch_cuda(report)

    print("[3/4] CTranslate2のCUDAデバイス検出を確認中...")
    check_ctranslate2(report)

    print(f"[4/4] faster-whisperをGPU(cuda)でロード中(model={model_size}, compute_type={compute_type})...")
    gpu_check = check_faster_whisper_load(report, model_size, compute_type, device="cuda")
    report.layers.append(gpu_check)
    report.gpu_load_ok = gpu_check.ok

    if not report.gpu_load_ok and not skip_cpu_fallback:
        print("[cpu-fallback] GPUで失敗したため、CPUで動くかどうかも確認します(切り分け用)...")
        cpu_check = check_faster_whisper_load(report, model_size, "int8", device="cpu")
        report.layers.append(cpu_check)
        report.cpu_load_ok = cpu_check.ok

    _build_conclusion(report)
    return report


def _build_conclusion(report: CheckReport) -> None:
    if report.gpu_load_ok:
        report.conclusion = "faster-whisperはGPU(cuda)で正常に動作した。sm_120は問題にならなかった。"
        report.recommendation = (
            "9日目③のSTT選定を、このGPU構成(faster-whisper + cuda)を前提に進めてよい。"
            "device='cuda'で採用モデルサイズ(small/medium/kotoba等)を本ベンチにも通してstt_bench.pyへ進む。"
        )
        return

    ct2_layer = next((l for l in report.layers if l.name.startswith("CTranslate2")), None)
    nvsmi_layer = next((l for l in report.layers if l.name.startswith("nvidia-smi")), None)

    if nvsmi_layer is not None and not nvsmi_layer.ok:
        report.conclusion = "GPUロードに失敗。原因はnvidia-smiの時点でGPUが認識されていないこと(ドライバの問題)。"
        report.recommendation = "faster-whisper以前の問題。NVIDIAドライバの再インストール・PCの再起動を先に試す。"
    elif ct2_layer is not None and not ct2_layer.ok:
        report.conclusion = "GPUロードに失敗。CTranslate2自体がCUDAデバイスを検出できていない。"
        report.recommendation = (
            "CTranslate2がsm_120(Blackwell)に未対応の可能性が高い。CTranslate2の最新リリースノート/Issueを確認し、"
            "対応版があればアップグレードして再検証する。対応版がなければ代替(whisper.cppのCUDA/Vulkanビルド、"
            "CPU int8実行)へ切り替える。"
        )
    else:
        report.conclusion = "CTranslate2はCUDAデバイスを検出できているが、WhisperModelのロードまたは推論で失敗した。"
        report.recommendation = (
            "compute_type(int8_float16以外の値)やモデルサイズを変えて再検証する。"
            "cuDNNのバージョン不一致の可能性もあるため、エラーメッセージのtracebackを確認する。"
        )

    if report.cpu_load_ok is True:
        report.recommendation += " なおCPU(int8)では動作したため、最悪の場合はCPU実行でSTTパイプライン自体は組める(速度は要再測定)。"
    elif report.cpu_load_ok is False:
        report.recommendation += " CPUでも失敗しているため、faster-whisperのインストール自体に問題がある可能性が高い(pip再インストールを推奨)。"


def format_markdown(report: CheckReport, model_size: str, compute_type: str) -> str:
    lines: list[str] = []
    lines.append(f"# faster-whisper GPU(sm_120)検証結果 ({datetime.now().strftime('%Y-%m-%d %H:%M:%S')})")
    lines.append("")
    lines.append(f"- OS: `{platform.platform()}`")
    lines.append(f"- Python: `{platform.python_version()}`")
    lines.append(f"- 検証モデル: `{model_size}` / compute_type: `{compute_type}`")
    lines.append("")
    lines.append("## 各レイヤーの検証結果")
    lines.append("")
    lines.append("| レイヤー | 結果 | 詳細 |")
    lines.append("|---|---|---|")
    for layer in report.layers:
        result = "OK" if layer.ok else "**NG**"
        detail = layer.detail.replace("|", "\\|").replace("\n", "<br>")
        lines.append(f"| {layer.name} | {result} | {detail} |")
    lines.append("")

    lines.append("## 結論")
    lines.append("")
    lines.append(f"**{report.conclusion}**")
    lines.append("")
    lines.append(f"改善策/次のアクション: {report.recommendation}")
    lines.append("")

    tracebacks = [l for l in report.layers if l.error_traceback]
    if tracebacks:
        lines.append("## エラー詳細(traceback)")
        lines.append("")
        for layer in tracebacks:
            lines.append(f"### {layer.name}")
            lines.append("")
            lines.append("```")
            lines.append(layer.error_traceback.strip())
            lines.append("```")
            lines.append("")

    return "\n".join(lines)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="faster-whisper(CTranslate2)がsm_120 GPUで動くかを層ごとに切り分けて検証する"
    )
    parser.add_argument("--model", default=DEFAULT_MODEL, help=f"検証に使うWhisperモデルサイズ(既定: {DEFAULT_MODEL})")
    parser.add_argument(
        "--compute-type", default=DEFAULT_COMPUTE_TYPE, help=f"CTranslate2のcompute_type(既定: {DEFAULT_COMPUTE_TYPE})"
    )
    parser.add_argument(
        "--skip-cpu-fallback",
        action="store_true",
        help="GPU検証で失敗した場合のCPUフォールバック検証をスキップする(GPU検証のみで速く終わらせたい場合)",
    )
    parser.add_argument(
        "--output-dir", default=str(DEFAULT_OUTPUT_DIR), help="出力先ディレクトリ(既定: scripts/results/gpu_check)"
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    report = run_all_checks(
        model_size=args.model, compute_type=args.compute_type, skip_cpu_fallback=args.skip_cpu_fallback
    )
    markdown = format_markdown(report, args.model, args.compute_type)

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    out_path = output_dir / f"gpu_check_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
    out_path.write_text(markdown, encoding="utf-8")

    print()
    print(markdown)
    print(f"[check_faster_whisper_gpu] 結果を保存しました: {out_path}")

    return 0 if report.gpu_load_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
