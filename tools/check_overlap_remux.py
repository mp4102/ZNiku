"""用短合成 ProRes 验证无重编码 context/crop；不实现生产节点或外部 AI。

实验只支持 18/31 帧、30/1 与 30000/1001，三个章含一帧短章。noise amount=0 仅按
packet 序号丢弃区间外包，setts 重建声明 CFR 时间轴；逐帧 decoded SHA-256 与 packet
payload SHA-256 必须完全对应，不用仅总帧数正确代替内容正确。所有源、raw、失败反例保留。
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from dataclasses import asdict, dataclass
from fractions import Fraction
from pathlib import Path
from time import perf_counter
from typing import Any, cast


@dataclass(frozen=True, slots=True)
class Fingerprints:
    """只属于短实验的逐帧 oracle，不引入生产 Evidence 或全片 hash 门禁。"""

    packets: tuple[str, ...]
    decoded: tuple[str, ...]


class RemuxExperiment:
    """有界、无覆盖的合成媒体实验；进程失败与 oracle 不符均直接抛错。"""

    def __init__(self, output: Path) -> None:
        if output.exists():
            raise FileExistsError("E_REMUX_OUTPUT_EXISTS: 必须选择新的实验目录")
        ffmpeg = shutil.which("ffmpeg")
        ffprobe = shutil.which("ffprobe")
        if ffmpeg is None or ffprobe is None:
            raise RuntimeError("E_REMUX_TOOLS: 实验需要 FFmpeg/FFprobe")
        self.ffmpeg = ffmpeg
        self.ffprobe = ffprobe
        output.mkdir(parents=True, exist_ok=False)
        self.output = output.resolve(strict=True)
        self.commands: list[dict[str, object]] = []

    def target(self, name: str) -> Path:
        """实验文件必须是新目录内的单层 MOV 名称，不允许调用方向外写入。"""

        if (
            Path(name).name != name
            or any(char in name for char in "/\\:\r\n")
            or not name.endswith(".mov")
        ):
            raise ValueError("E_REMUX_TARGET: 必须是单层 MOV 文件名")
        return self.output / name

    def source(self, path: Path) -> Path:
        """只读输入必须解析到本实验单层真实文件；软链接也不能引向用户媒体。"""

        resolved = path.resolve(strict=True)
        if resolved.parent != self.output or not resolved.is_file():
            raise ValueError("E_REMUX_SOURCE: 只允许本实验目录内的已存在文件")
        return resolved

    def run(self, executable: str, argv: list[str], *, role: str) -> bytes:
        """固定工具、argv 与超时；媒体范围很小，stdout 仅为有界 probe/hash 文本。"""

        if executable not in (self.ffmpeg, self.ffprobe) or len(argv) > 64:
            raise ValueError("E_REMUX_COMMAND: 超出实验命令边界")
        started = perf_counter()
        result = subprocess.run(
            [executable, *argv],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            shell=False,
            timeout=30,
            check=False,
        )
        self.commands.append({"role": role, "argv": argv, "seconds": perf_counter() - started})
        if result.returncode:
            raise RuntimeError(
                f"E_REMUX_TOOL_FAILED: {role}: "
                + result.stderr.decode("utf-8", errors="replace")[-3000:]
            )
        return result.stdout

    def generate(self, name: str, rate: Fraction, count: int, *, offset: int, step: int) -> Path:
        """唯一逐帧灰阶标记；raw FI 只是已知半帧坐标的合成形状，不是 AI 推理。"""

        if not 1 <= count <= 61 or not 0 <= offset <= 60 or step not in (1, 2):
            raise ValueError("E_REMUX_SYNTHETIC_RANGE: 超出短合成预算")
        target = self.target(name)
        self.run(
            self.ffmpeg,
            [
                "-hide_banner",
                "-loglevel",
                "error",
                "-nostdin",
                "-n",
                "-f",
                "lavfi",
                "-i",
                f"nullsrc=size=64x48:rate={rate}",
                "-vf",
                f"geq=lum='16+({offset}+N*{step})*3':cb=128:cr=128",
                "-frames:v",
                str(count),
                "-an",
                "-c:v",
                "prores_ks",
                "-profile:v",
                "3",
                "-qscale:v",
                "2",
                "-pix_fmt",
                "yuv422p10le",
                "-threads",
                "1",
                "-video_track_timescale",
                str(rate.numerator),
                str(target),
            ],
            role="generate-synthetic-not-AI",
        )
        return target

    @staticmethod
    def clock_filter(rate: Fraction) -> str:
        """仅对已验证一包一帧的 CFR ProRes 重设 PTS/DTS，不改变包内压缩图像。"""

        return (
            f"setts=time_base=1/{rate.numerator}:"
            f"ts=N*{rate.denominator}:duration={rate.denominator}"
        )

    def copy_range(self, source: Path, name: str, start: int, end: int, rate: Fraction) -> Path:
        """按半开 packet/frame 区间 stream-copy；不会过滤像素或调用编码器。"""

        if not 0 <= start < end <= 61:
            raise ValueError("E_REMUX_COPY_RANGE: 只允许本实验短媒体的有效范围")
        source = self.source(source)
        target = self.target(name)
        bitstream = f"noise=amount=0:drop='lt(n,{start})+gte(n,{end})'," + self.clock_filter(rate)
        self.run(
            self.ffmpeg,
            [
                "-hide_banner",
                "-loglevel",
                "error",
                "-nostdin",
                "-n",
                "-i",
                str(source),
                "-map",
                "0:v:0",
                "-an",
                "-c:v",
                "copy",
                "-bsf:v",
                bitstream,
                "-video_track_timescale",
                str(rate.numerator),
                str(target),
            ],
            role="stream-copy-frame-range",
        )
        return target

    def join(self, sources: tuple[Path, ...], name: str, rate: Fraction) -> Path:
        """有界 ffconcat 顺序读取；重建 exact CFR 时钟后独立验证每个 PTS。"""

        if not 1 <= len(sources) <= 3:
            raise ValueError("E_REMUX_CONCAT_BOUND: 只允许本实验最多三个文件")
        sources = tuple(self.source(path) for path in sources)
        target = self.target(name)
        manifest = self.output / f"{name}.ffconcat"
        with manifest.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write("ffconcat version 1.0\n")
            for source in sources:
                if any(character in source.name for character in "'\\\r\n"):
                    raise ValueError("E_REMUX_CONCAT_NAME: 不允许清单转义")
                stream.write(f"file '{source.name}'\n")
        self.run(
            self.ffmpeg,
            [
                "-hide_banner",
                "-loglevel",
                "error",
                "-nostdin",
                "-n",
                "-f",
                "concat",
                "-safe",
                "1",
                "-i",
                str(manifest),
                "-map",
                "0:v:0",
                "-an",
                "-c:v",
                "copy",
                "-bsf:v",
                self.clock_filter(rate),
                "-video_track_timescale",
                str(rate.numerator),
                str(target),
            ],
            role="stream-copy-concat",
        )
        return target

    def inspect(self, path: Path, rate: Fraction, count: int) -> Fingerprints:
        """独立读取 payload hash、decoded hash 和 exact 时钟；不容忍近似或一帧偏移。"""

        if not 1 <= count <= 61:
            raise ValueError("E_REMUX_ORACLE_BOUND: 只检查本实验短媒体")
        path = self.source(path)
        raw = self.run(
            self.ffprobe,
            [
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_streams",
                "-show_packets",
                "-show_data_hash",
                "sha256",
                "-show_entries",
                "stream=codec_name,avg_frame_rate,r_frame_rate,time_base:"
                "packet=pts,dts,duration,flags,data_hash",
                "-of",
                "json",
                str(path),
            ],
            role="oracle-packets",
        )
        document = cast(dict[str, Any], json.loads(raw))
        streams, packets = document["streams"], document["packets"]
        if len(streams) != 1 or streams[0]["codec_name"] != "prores" or len(packets) != count:
            raise AssertionError("E_REMUX_PACKET_COUNT: 不是预期 ProRes 一包一帧")
        stream = streams[0]
        if any(Fraction(stream[key]) != rate for key in ("avg_frame_rate", "r_frame_rate")):
            raise AssertionError("E_REMUX_FPS: exact FPS 不符")
        time_base = Fraction(stream["time_base"])
        for index, packet in enumerate(packets):
            if (
                Fraction(packet["pts"]) * time_base != index / rate
                or Fraction(packet["dts"]) * time_base != index / rate
                or Fraction(packet["duration"]) * time_base != 1 / rate
                or "K" not in packet["flags"]
            ):
                raise AssertionError(f"E_REMUX_TIMELINE: packet {index} PTS/DTS/duration/key 不符")
        hashes = self.run(
            self.ffmpeg,
            [
                "-hide_banner",
                "-loglevel",
                "error",
                "-nostdin",
                "-i",
                str(path),
                "-map",
                "0:v:0",
                "-an",
                "-c:v",
                "rawvideo",
                "-pix_fmt",
                "yuv422p10le",
                "-fps_mode",
                "passthrough",
                "-f",
                "framehash",
                "-hash",
                "sha256",
                "pipe:1",
            ],
            role="oracle-decoded-framehash",
        ).decode("ascii")
        rows = [
            line.split(",") for line in hashes.splitlines() if line and not line.startswith("#")
        ]
        tb_line = next(line for line in hashes.splitlines() if line.startswith("#tb 0:"))
        decoded_time_base = Fraction(tb_line.split(":", 1)[1].strip())
        if len(rows) != count or any(
            int(row[2]) * decoded_time_base != index / rate for index, row in enumerate(rows)
        ):
            raise AssertionError("E_REMUX_DECODED_TIMELINE: 解码帧 count/PTS 不符")
        return Fingerprints(
            packets=tuple(packet["data_hash"] for packet in packets),
            decoded=tuple(row[-1].strip() for row in rows),
        )


def require_match(actual: Fingerprints, expected: Fingerprints) -> None:
    """相同长度但错一帧也失败；必须同时保留压缩 payload 和解码内容。"""

    if actual != expected:
        raise AssertionError("E_REMUX_CONTENT: packet payload 或 decoded frame 内容/顺序不一致")


def slice_fingerprints(source: Fingerprints, start: int, end: int) -> Fingerprints:
    return Fingerprints(source.packets[start:end], source.decoded[start:end])


def run_experiment(
    output: Path, *, source_frames: int = 18, frame_rate: str = "30/1"
) -> dict[str, Any]:
    """三章短样本验证；上下文宽度 2 只是实验值，不冻结任何 Aion profile。"""

    if source_frames not in (18, 31) or frame_rate not in ("30/1", "30000/1001"):
        raise ValueError("E_REMUX_EXPERIMENT_BUDGET: 只允许两种短帧数和两个 exact FPS")
    experiment = RemuxExperiment(output)
    rate = Fraction(frame_rate)
    version = (
        experiment.run(experiment.ffmpeg, ["-version"], role="version").decode().splitlines()[0]
    )
    source = experiment.generate("source.mov", rate, source_frames, offset=0, step=2)
    source_hashes = experiment.inspect(source, rate, source_frames)
    if len(set(source_hashes.decoded)) != source_frames:
        raise AssertionError("E_REMUX_ORACLE_NOT_UNIQUE: 合成每帧必须可区分")
    reference = experiment.generate(
        "synthetic-fi-reference.mov", rate * 2, 2 * source_frames - 1, offset=0, step=1
    )
    reference_hashes = experiment.inspect(reference, rate * 2, 2 * source_frames - 1)
    ranges = ((0, 1), (1, 3), (3, source_frames))
    chapters: list[Path] = []
    for index, (start, end) in enumerate(ranges):
        chapter = experiment.copy_range(source, f"chapter-{index}.mov", start, end, rate)
        require_match(
            experiment.inspect(chapter, rate, end - start),
            slice_fingerprints(source_hashes, start, end),
        )
        chapters.append(chapter)
    cropped: list[Path] = []
    projections: list[dict[str, Any]] = []
    wrong_crop_rejected = False
    for index, (start, end) in enumerate(ranges):
        context_start, context_end = max(0, start - 2), min(source_frames, end + 2)
        pieces: list[Path] = []
        for other, (left, right) in enumerate(ranges):
            first, last = max(left, context_start), min(right, context_end)
            if first < last:
                piece = experiment.copy_range(
                    chapters[other],
                    f"context-{index}-piece-{other}.mov",
                    first - left,
                    last - left,
                    rate,
                )
                require_match(
                    experiment.inspect(piece, rate, last - first),
                    slice_fingerprints(source_hashes, first, last),
                )
                pieces.append(piece)
        context = experiment.join(tuple(pieces), f"context-{index}.mov", rate)
        context_count = context_end - context_start
        require_match(
            experiment.inspect(context, rate, context_count),
            slice_fingerprints(source_hashes, context_start, context_end),
        )
        raw_count = 2 * context_count - 1
        raw = experiment.generate(
            f"synthetic-fi-raw-{index}.mov", rate * 2, raw_count, offset=2 * context_start, step=1
        )
        raw_hashes = experiment.inspect(raw, rate * 2, raw_count)
        require_match(
            raw_hashes, slice_fingerprints(reference_hashes, 2 * context_start, 2 * context_end - 1)
        )
        crop_start = 2 * (start - context_start)
        crop_end = 2 * (end - context_start) - int(end == source_frames)
        crop = experiment.copy_range(raw, f"crop-{index}.mov", crop_start, crop_end, rate * 2)
        require_match(
            experiment.inspect(crop, rate * 2, crop_end - crop_start),
            slice_fingerprints(raw_hashes, crop_start, crop_end),
        )
        cropped.append(crop)
        projections.append(
            {
                "chapter": [start, end],
                "context": [context_start, context_end],
                "context_piece_count": len(pieces),
                "raw_frames": raw_count,
                "crop": [crop_start, crop_end],
            }
        )
        if index == 0:
            wrong = experiment.copy_range(
                raw, "wrong-equal-length-crop.mov", crop_start + 1, crop_end + 1, rate * 2
            )
            try:
                require_match(
                    experiment.inspect(wrong, rate * 2, crop_end - crop_start),
                    slice_fingerprints(raw_hashes, crop_start, crop_end),
                )
            except AssertionError as error:
                if "E_REMUX_CONTENT" not in str(error):
                    raise
                wrong_crop_rejected = True
    if not wrong_crop_rejected:
        raise AssertionError("E_REMUX_ORACLE_NEGATIVE: oracle 未拒绝等长错裁，不得报告实验成功")
    final = experiment.join(tuple(cropped), "cropped-sequence.mov", rate * 2)
    require_match(experiment.inspect(final, rate * 2, 2 * source_frames - 1), reference_hashes)
    require_match(experiment.inspect(source, rate, source_frames), source_hashes)
    report: dict[str, Any] = {
        "ffmpeg_version": version,
        "source_frames": source_frames,
        "frame_rate": frame_rate,
        "chapter_ranges": projections,
        "cropped_sequence_frames": 2 * source_frames - 1,
        "pixel_and_packet_exact": True,
        "exact_fps_pts_dts_duration": True,
        "wrong_equal_length_crop_rejected": wrong_crop_rejected,
        "external_AI_verified": False,
        "production_nodes_implemented": False,
        "scope": "short synthetic ProRes only; packet scanning, not production seek optimization",
        "files_bytes": {path.name: path.stat().st_size for path in experiment.output.glob("*.mov")},
        "commands": experiment.commands,
        "source_oracle": asdict(source_hashes),
    }
    with (experiment.output / "report.json").open("x", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
    return report


def main() -> int:
    """新目录输出有限实验；错误非零退出并保留所有源、raw 与中间文件。"""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source-frames", type=int, choices=(18, 31), default=18)
    parser.add_argument("--frame-rate", choices=("30/1", "30000/1001"), default="30/1")
    args = parser.parse_args()
    report = run_experiment(
        args.output, source_frames=args.source_frames, frame_rate=args.frame_rate
    )
    print(
        json.dumps(
            {
                key: value
                for key, value in report.items()
                if key not in {"commands", "source_oracle"}
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
