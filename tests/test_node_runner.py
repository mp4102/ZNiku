"""验证 0.2.0 Node Runner 的 attempt 隔离、执行与轻量验收边界。

测试仅使用临时目录、当前 Python 解释器与合成小文件；不访问真实媒体，不依赖持久化 Service，也不把
失败 attempt 的部分文件解释为 Artifact 或可恢复 checkpoint。
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

import pytest
from pydantic import JsonValue

from zniku.graph import (
    Cardinality,
    CommandExecutorSpec,
    ExecutionMode,
    ManualExternalExecutorSpec,
    NodeDefinition,
    NodeInstance,
    PortSpec,
    PythonExecutorSpec,
    ValidatorSpec,
)
from zniku.runtime.runner import (
    ManualHandoff,
    ManualSubmission,
    NodeExecutionRequest,
    NodeRunner,
    NodeValidatorContext,
    NodeValidatorResult,
    OutputPathSpec,
    ProducedOutput,
    PythonAdapterContext,
    PythonAdapterResult,
    RunnerCancelled,
    RunnerError,
    RunnerFailureReason,
    RunnerInput,
)


def request_for(
    definition: NodeDefinition,
    *,
    inputs: tuple[RunnerInput, ...] = (),
    node_run_id: str | None = None,
    output_paths: tuple[OutputPathSpec, ...] = (),
    parameters: dict[str, JsonValue] | None = None,
) -> NodeExecutionRequest:
    """构造绑定精确定义版本的独立 attempt 请求。"""

    node = NodeInstance(
        node_id="same-human-node-id",
        type_id=definition.type_id,
        definition_version=definition.version,
        parameters=parameters or {},
    )
    return NodeExecutionRequest(
        node_run_id=node_run_id or str(uuid4()),
        attempt=1,
        definition=definition,
        node=node,
        inputs=inputs,
        output_paths=output_paths,
    )


def python_data_definition(*, outputs: tuple[str, ...] = ("data",)) -> NodeDefinition:
    """创建不触发 FFprobe 的合成 Python 节点。"""

    return NodeDefinition(
        type_id="test.python_data",
        version="2.0.0",
        output_ports=tuple(PortSpec(port_id=item, data_type="DataFile") for item in outputs),
        execution_mode=ExecutionMode.AUTOMATIC,
        executor=PythonExecutorSpec(adapter="tests.adapters:write"),
    )


def test_python_adapter_uses_attempt_local_defaults_and_attempts_are_isolated(
    tmp_path: Path,
) -> None:
    contexts: list[PythonAdapterContext] = []

    def adapter(context: PythonAdapterContext) -> PythonAdapterResult:
        contexts.append(context)
        context.outputs[0].path.write_bytes(b"synthetic-data")
        context.stdout_log_path.write_text("adapter stdout\n", encoding="utf-8")
        return PythonAdapterResult(validation_summary={"adapter": "passed"})

    definition = python_data_definition()
    runner = NodeRunner(
        tmp_path / "attempts",
        python_adapters={"tests.adapters:write": adapter},
        media_probe=lambda _path, _kind: pytest.fail("DataFile 不得调用媒体 probe"),
    )
    first = runner.run_automatic(request_for(definition))
    second = runner.run_automatic(request_for(definition))

    assert first.work_dir != second.work_dir
    assert first.work_dir.parent == (tmp_path / "attempts").resolve()
    assert "same-human-node-id" not in str(first.work_dir)
    assert first.artifacts[0].path.parent.name == "outputs"
    assert first.artifacts[0].artifact_id != second.artifacts[0].artifact_id
    assert first.artifacts[0].path.read_bytes() == b"synthetic-data"
    assert first.stdout_log_path.read_text(encoding="utf-8") == "adapter stdout\n"
    assert contexts[0].outputs[0].port_id == "data"


def test_command_expands_strict_placeholders_as_argv_and_writes_logs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    single = tmp_path / "single input.txt"
    many_a = tmp_path / "ordered A.txt"
    many_b = tmp_path / "ordered B.txt"
    for path, payload in ((single, b"one"), (many_a, b"a"), (many_b, b"b")):
        path.write_bytes(payload)

    code = (
        "from pathlib import Path; import sys; "
        "Path(sys.argv[5]).write_text('\\n'.join(sys.argv[1:5] + [sys.argv[6]]), "
        "encoding='utf-8'); print('ordinary stdout'); "
        "print('ordinary stderr', file=sys.stderr)"
    )
    definition = NodeDefinition(
        type_id="test.command",
        version="2.0.0",
        input_ports=(
            PortSpec(port_id="single", data_type="DataFile", required=True),
            PortSpec(
                port_id="many",
                data_type="DataFile",
                cardinality=Cardinality.ORDERED_MANY,
                required=True,
            ),
        ),
        output_ports=(PortSpec(port_id="out", data_type="DataFile"),),
        parameter_schema={
            "type": "object",
            "properties": {"value": {"type": "string"}},
            "required": ["value"],
            "additionalProperties": False,
        },
        execution_mode=ExecutionMode.AUTOMATIC,
        executor=CommandExecutorSpec(
            executable=sys.executable,
            argv=(
                "-c",
                code,
                "{workdir}",
                "{input:single}",
                "{inputs:many}",
                "{output:out}",
                "{param:value}",
            ),
        ),
    )
    inputs = (
        RunnerInput("single", "artifact.single", "DataFile", single),
        RunnerInput("many", "artifact.a", "DataFile", many_a, ordinal=0),
        RunnerInput("many", "artifact.b", "DataFile", many_b, ordinal=1),
    )

    real_run = subprocess.run
    calls: list[tuple[object, dict[str, object]]] = []

    def spy_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        calls.append((args[0], dict(kwargs)))
        return real_run(*args, **kwargs)  # type: ignore[call-overload,no-any-return]

    monkeypatch.setattr(subprocess, "run", spy_run)
    result = NodeRunner(tmp_path / "work").run_automatic(
        request_for(
            definition,
            inputs=inputs,
            output_paths=(OutputPathSpec("out", "nested/result.txt"),),
            parameters={"value": "literal & shell | text"},
        )
    )

    assert len(calls) == 1
    command, options = calls[0]
    assert isinstance(command, list)
    assert options["shell"] is False
    assert command[-1] == "literal & shell | text"
    output_lines = result.artifacts[0].path.read_text(encoding="utf-8").splitlines()
    assert output_lines[1:] == [str(single), str(many_a), str(many_b), "literal & shell | text"]
    assert result.stdout_log_path.read_text(encoding="utf-8").strip() == "ordinary stdout"
    assert result.stderr_log_path.read_text(encoding="utf-8").strip() == "ordinary stderr"


def test_command_preserves_literal_braces_and_can_escape_exact_placeholder(
    tmp_path: Path,
) -> None:
    code = (
        "from pathlib import Path; import json, sys; "
        "Path(sys.argv[1]).write_text(json.dumps(sys.argv[2:]), encoding='utf-8')"
    )
    definition = NodeDefinition(
        type_id="test.command.literal_braces",
        version="1.0.0",
        output_ports=(PortSpec(port_id="out", data_type="DataFile"),),
        execution_mode=ExecutionMode.AUTOMATIC,
        executor=CommandExecutorSpec(
            executable=sys.executable,
            argv=(
                "-c",
                code,
                "{output:out}",
                '{"template":"{value}"}',
                "{{workdir}}",
                "{unknown:value}",
                "prefix-{workdir}",
            ),
        ),
    )

    result = NodeRunner(tmp_path / "work").run_automatic(request_for(definition))

    assert json.loads(result.artifacts[0].path.read_text("utf-8")) == [
        '{"template":"{value}"}',
        "{workdir}",
        "{unknown:value}",
        "prefix-{workdir}",
    ]


def test_missing_one_of_multiple_outputs_returns_no_partial_result(tmp_path: Path) -> None:
    definition = python_data_definition(outputs=("first", "second"))

    def partial_adapter(context: PythonAdapterContext) -> PythonAdapterResult:
        context.outputs[0].path.write_bytes(b"partial")
        return PythonAdapterResult()

    runner = NodeRunner(
        tmp_path / "work", python_adapters={"tests.adapters:write": partial_adapter}
    )
    with pytest.raises(RunnerError) as captured:
        runner.run_automatic(request_for(definition))

    assert captured.value.code == "E_RUNNER_OUTPUT_MISSING"
    assert captured.value.reason is RunnerFailureReason.OUTPUT_INVALID
    assert not hasattr(captured.value, "artifacts")


def test_nonzero_command_and_adapter_cancellation_are_structured_failures(
    tmp_path: Path,
) -> None:
    command_definition = NodeDefinition(
        type_id="test.command_failure",
        version="2.0.0",
        execution_mode=ExecutionMode.AUTOMATIC,
        executor=CommandExecutorSpec(
            executable=sys.executable,
            argv=("-c", "import sys; print('failed'); sys.exit(7)"),
        ),
    )
    with pytest.raises(RunnerError) as command_error:
        NodeRunner(tmp_path / "command").run_automatic(request_for(command_definition))
    assert command_error.value.code == "E_RUNNER_PROCESS_EXIT_NONZERO"
    assert command_error.value.exit_code == 7
    assert command_error.value.stdout_log_path is not None
    assert command_error.value.stdout_log_path.read_text(encoding="utf-8").strip() == "failed"

    definition = python_data_definition()

    def cancelled(_context: PythonAdapterContext) -> PythonAdapterResult:
        raise RunnerCancelled("operator requested cancellation")

    with pytest.raises(RunnerError) as cancelled_error:
        NodeRunner(
            tmp_path / "python",
            python_adapters={"tests.adapters:write": cancelled},
        ).run_automatic(request_for(definition))
    assert cancelled_error.value.reason is RunnerFailureReason.CANCELLED
    assert cancelled_error.value.code == "E_RUNNER_CANCELLED"


def test_command_nul_and_subprocess_value_error_are_structured_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    nul_definition = NodeDefinition(
        type_id="test.command_nul",
        version="2.0.0",
        execution_mode=ExecutionMode.AUTOMATIC,
        executor=CommandExecutorSpec(executable=sys.executable, argv=("-V",)),
    )
    object.__setattr__(
        nul_definition,
        "executor",
        CommandExecutorSpec.model_construct(
            kind="command",
            executable=sys.executable,
            argv=("\x00",),
        ),
    )
    with pytest.raises(RunnerError) as nul_error:
        NodeRunner(tmp_path / "command-nul").run_automatic(request_for(nul_definition))
    assert nul_error.value.code == "E_RUNNER_COMMAND_NUL"
    assert nul_error.value.reason is RunnerFailureReason.CONFIGURATION

    invalid_start = NodeDefinition(
        type_id="test.command_value_error",
        version="2.0.0",
        execution_mode=ExecutionMode.AUTOMATIC,
        executor=CommandExecutorSpec(executable=sys.executable, argv=("-V",)),
    )

    def raise_value_error(*_args: object, **_kwargs: object) -> None:
        raise ValueError("synthetic subprocess configuration error")

    monkeypatch.setattr(subprocess, "run", raise_value_error)
    with pytest.raises(RunnerError) as start_error:
        NodeRunner(tmp_path / "value-error").run_automatic(request_for(invalid_start))
    assert start_error.value.code == "E_RUNNER_PROCESS_START_FAILED"
    assert start_error.value.reason is RunnerFailureReason.PROCESS_FAILED


def test_python_adapter_result_fields_fail_closed(tmp_path: Path) -> None:
    definition = python_data_definition()
    invalid_results = (
        PythonAdapterResult(outputs=(cast(Any, object()),)),
        PythonAdapterResult(media_summary=cast(Any, None)),
        PythonAdapterResult(validation_summary=cast(Any, [])),
        PythonAdapterResult(media_summary=cast(Any, {1: "non-string-key"})),
    )

    for index, invalid_result in enumerate(invalid_results):

        def invalid_adapter(
            context: PythonAdapterContext,
            *,
            result: PythonAdapterResult = invalid_result,
        ) -> PythonAdapterResult:
            context.outputs[0].path.write_bytes(b"candidate")
            return result

        with pytest.raises(RunnerError) as captured:
            NodeRunner(
                tmp_path / f"adapter-{index}",
                python_adapters={"tests.adapters:write": invalid_adapter},
            ).run_automatic(request_for(definition))
        assert captured.value.code == "E_RUNNER_ADAPTER_RESULT_INVALID"
        assert captured.value.reason is RunnerFailureReason.ADAPTER_FAILED


def test_validator_result_fields_fail_closed(tmp_path: Path) -> None:
    def adapter(context: PythonAdapterContext) -> PythonAdapterResult:
        context.outputs[0].path.write_bytes(b"candidate")
        return PythonAdapterResult()

    definition = NodeDefinition(
        type_id="test.validator_contract",
        version="2.0.0",
        output_ports=(PortSpec(port_id="data", data_type="DataFile"),),
        execution_mode=ExecutionMode.AUTOMATIC,
        executor=PythonExecutorSpec(adapter="tests.adapters:validator-contract"),
        validator=ValidatorSpec(adapter="tests.validators:contract"),
    )
    invalid_results = (
        NodeValidatorResult(passed=cast(Any, "false")),
        NodeValidatorResult(passed=cast(Any, 1)),
        NodeValidatorResult(passed=True, summary=cast(Any, None)),
        NodeValidatorResult(passed=True, summary=cast(Any, {1: "invalid"})),
        NodeValidatorResult(passed=True, warnings=cast(Any, ["not-a-tuple"])),
        NodeValidatorResult(passed=True, warnings=(cast(Any, 1),)),
        NodeValidatorResult(passed=True, message=cast(Any, 1)),
    )

    for index, invalid_result in enumerate(invalid_results):

        def invalid_validator(
            _context: NodeValidatorContext,
            *,
            result: NodeValidatorResult = invalid_result,
        ) -> NodeValidatorResult:
            return result

        with pytest.raises(RunnerError) as captured:
            NodeRunner(
                tmp_path / f"validator-{index}",
                python_adapters={"tests.adapters:validator-contract": adapter},
                validators={"tests.validators:contract": invalid_validator},
            ).run_automatic(request_for(definition))
        assert captured.value.code == "E_RUNNER_VALIDATOR_RESULT_INVALID"
        assert captured.value.reason is RunnerFailureReason.VALIDATOR_FAILED


def test_plugin_system_exit_is_confined_to_current_attempt(tmp_path: Path) -> None:
    """插件主动退出只能形成 RunnerError，不能终止宿主进程。"""

    def exit_adapter(_context: PythonAdapterContext) -> PythonAdapterResult:
        raise SystemExit(7)

    data_definition = python_data_definition()
    with pytest.raises(RunnerError) as adapter_error:
        NodeRunner(
            tmp_path / "adapter-exit",
            python_adapters={"tests.adapters:write": exit_adapter},
        ).run_automatic(request_for(data_definition))
    assert adapter_error.value.code == "E_RUNNER_ADAPTER_FAILED"
    assert adapter_error.value.reason is RunnerFailureReason.ADAPTER_FAILED

    def writing_adapter(context: PythonAdapterContext) -> PythonAdapterResult:
        context.outputs[0].path.write_bytes(b"candidate")
        return PythonAdapterResult()

    def exit_validator(_context: NodeValidatorContext) -> NodeValidatorResult:
        raise SystemExit(8)

    validated_definition = NodeDefinition(
        type_id="test.validator_system_exit",
        version="2.0.0",
        output_ports=(PortSpec(port_id="data", data_type="DataFile"),),
        execution_mode=ExecutionMode.AUTOMATIC,
        executor=PythonExecutorSpec(adapter="tests.adapters:validator-exit"),
        validator=ValidatorSpec(adapter="tests.validators:exit"),
    )
    with pytest.raises(RunnerError) as validator_error:
        NodeRunner(
            tmp_path / "validator-exit",
            python_adapters={"tests.adapters:validator-exit": writing_adapter},
            validators={"tests.validators:exit": exit_validator},
        ).run_automatic(request_for(validated_definition))
    assert validator_error.value.code == "E_RUNNER_VALIDATOR_FAILED"
    assert validator_error.value.reason is RunnerFailureReason.VALIDATOR_FAILED

    def exit_probe(_path: Path, _kind: str) -> dict[str, object]:
        raise SystemExit(9)

    media_definition = NodeDefinition(
        type_id="test.probe_system_exit",
        version="2.0.0",
        output_ports=(PortSpec(port_id="video", data_type="VideoFile"),),
        execution_mode=ExecutionMode.AUTOMATIC,
        executor=PythonExecutorSpec(adapter="tests.adapters:probe-exit"),
    )
    with pytest.raises(RunnerError) as probe_error:
        NodeRunner(
            tmp_path / "probe-exit",
            python_adapters={"tests.adapters:probe-exit": writing_adapter},
            media_probe=exit_probe,
        ).run_automatic(request_for(media_definition))
    assert probe_error.value.code == "E_RUNNER_PROBE_FAILED"
    assert probe_error.value.reason is RunnerFailureReason.PROBE_FAILED


def test_manual_handoff_round_trips_across_runner_instance_and_submit(
    tmp_path: Path,
) -> None:
    definition = NodeDefinition(
        type_id="test.manual",
        version="2.0.0",
        output_ports=(PortSpec(port_id="result", data_type="DataFile"),),
        execution_mode=ExecutionMode.MANUAL_EXTERNAL,
        executor=ManualExternalExecutorSpec(instructions="请生成目标文件后提交"),
    )
    request = request_for(
        definition,
        output_paths=(OutputPathSpec("result", "external/result.bin"),),
    )
    root = tmp_path / "manual-work"
    handoff = NodeRunner(root).prepare_manual(request)
    restored = ManualHandoff.from_json(handoff.to_json())

    assert restored == handoff
    assert restored.instructions == "请生成目标文件后提交"
    target = Path(restored.outputs[0].path)
    target.write_bytes(b"manual-result")

    result = NodeRunner(root).submit_manual(request, restored)
    assert result.exit_code is None
    assert result.artifacts[0].path == target.resolve()
    assert result.artifacts[0].path.read_bytes() == b"manual-result"


def test_manual_submission_fields_fail_closed(tmp_path: Path) -> None:
    """畸形 external submission 只能形成 RunnerError，不能泄漏动态类型异常。"""

    definition = NodeDefinition(
        type_id="test.manual_submission_contract",
        version="2.0.0",
        output_ports=(PortSpec(port_id="result", data_type="DataFile"),),
        execution_mode=ExecutionMode.MANUAL_EXTERNAL,
        executor=ManualExternalExecutorSpec(),
    )
    request = request_for(definition)
    runner = NodeRunner(tmp_path / "manual-submission")
    handoff = runner.prepare_manual(request)
    Path(handoff.outputs[0].path).write_bytes(b"candidate")

    class ExitMapping(dict[str, object]):
        def items(self) -> Any:
            raise SystemExit(17)

    invalid_submissions = (
        cast(Any, object()),
        ManualSubmission(outputs=(cast(Any, object()),)),
        ManualSubmission(outputs=(ProducedOutput(port_id=cast(Any, 1), path=Path("candidate")),)),
        ManualSubmission(media_summary=cast(Any, None)),
        ManualSubmission(validation_summary={"nested": object()}),
        ManualSubmission(media_summary=cast(Any, ExitMapping())),
    )
    for submission in invalid_submissions:
        with pytest.raises(RunnerError) as captured:
            runner.submit_manual(request, handoff, submission)
        assert captured.value.code == "E_RUNNER_SUBMISSION_INVALID"
        assert captured.value.reason is RunnerFailureReason.VALIDATOR_FAILED


def test_manual_handoff_json_rejects_duplicate_nonstandard_and_invalid_numbers(
    tmp_path: Path,
) -> None:
    definition = NodeDefinition(
        type_id="test.manual_json",
        version="2.0.0",
        output_ports=(PortSpec(port_id="result", data_type="DataFile"),),
        execution_mode=ExecutionMode.MANUAL_EXTERNAL,
        executor=ManualExternalExecutorSpec(),
    )
    handoff = NodeRunner(tmp_path / "work").prepare_manual(request_for(definition))
    payload = handoff.to_json()

    duplicate = '{"schema_version":1,' + payload[1:]
    with pytest.raises(RunnerError, match="E_RUNNER_HANDOFF_INVALID"):
        ManualHandoff.from_json(duplicate)

    nonstandard = payload.replace('"instructions":null', '"instructions":NaN')
    with pytest.raises(RunnerError, match="E_RUNNER_HANDOFF_INVALID"):
        ManualHandoff.from_json(nonstandard)

    data = json.loads(payload)
    data["attempt"] = 0
    with pytest.raises(RunnerError, match="E_RUNNER_HANDOFF_INVALID"):
        ManualHandoff.from_json(json.dumps(data))

    data["attempt"] = 1
    data["inputs"] = [
        {
            "port_id": "input",
            "artifact_id": "artifact.input",
            "kind": "DataFile",
            "path": "input.bin",
            "ordinal": -1,
        }
    ]
    with pytest.raises(RunnerError, match="E_RUNNER_HANDOFF_INVALID"):
        ManualHandoff.from_json(json.dumps(data))


def test_path_escape_and_non_random_node_run_id_are_rejected(tmp_path: Path) -> None:
    definition = python_data_definition()
    runner = NodeRunner(
        tmp_path / "work",
        python_adapters={"tests.adapters:write": lambda _context: PythonAdapterResult()},
    )

    with pytest.raises(RunnerError, match="E_RUNNER_NODE_RUN_ID_INVALID"):
        runner.run_automatic(request_for(definition, node_run_id="../escape"))

    outside = tmp_path / "outside.bin"
    with pytest.raises(RunnerError, match="E_RUNNER_PATH_ESCAPE"):
        runner.run_automatic(
            request_for(
                definition,
                output_paths=(OutputPathSpec("data", "../../outside.bin"),),
            )
        )
    assert not outside.exists()


def test_media_probe_and_node_validator_gate_artifact_creation(tmp_path: Path) -> None:
    probe_calls: list[tuple[Path, str]] = []

    def adapter(context: PythonAdapterContext) -> PythonAdapterResult:
        context.outputs[0].path.write_bytes(b"synthetic-media")
        return PythonAdapterResult(media_summary={"adapter_model": "synthetic"})

    def probe(path: Path, kind: str) -> dict[str, object]:
        probe_calls.append((path, kind))
        return {"streams": [{"codec_type": "video"}]}

    def validator(context: NodeValidatorContext) -> NodeValidatorResult:
        assert context.outputs[0].size == len(b"synthetic-media")
        return NodeValidatorResult(
            passed=True,
            summary={"frame_relation": "passed"},
            warnings=("synthetic warning",),
        )

    definition = NodeDefinition(
        type_id="test.video",
        version="2.0.0",
        output_ports=(PortSpec(port_id="video", data_type="VideoFile"),),
        execution_mode=ExecutionMode.AUTOMATIC,
        executor=PythonExecutorSpec(adapter="tests.adapters:video"),
        validator=ValidatorSpec(adapter="tests.validators:video"),
    )
    result = NodeRunner(
        tmp_path / "work",
        python_adapters={"tests.adapters:video": adapter},
        validators={"tests.validators:video": validator},
        media_probe=probe,
    ).run_automatic(request_for(definition))

    assert probe_calls == [(result.artifacts[0].path, "VideoFile")]
    assert result.artifacts[0].media_info == {"streams": [{"codec_type": "video"}]}
    assert result.validation_summary["warnings"] == ["synthetic warning"]


def test_rejecting_node_validator_does_not_return_artifact(tmp_path: Path) -> None:
    def adapter(context: PythonAdapterContext) -> PythonAdapterResult:
        context.outputs[0].path.write_bytes(b"candidate")
        return PythonAdapterResult()

    definition = NodeDefinition(
        type_id="test.rejected",
        version="2.0.0",
        output_ports=(PortSpec(port_id="data", data_type="DataFile"),),
        execution_mode=ExecutionMode.AUTOMATIC,
        executor=PythonExecutorSpec(adapter="tests.adapters:rejected"),
        validator=ValidatorSpec(adapter="tests.validators:reject"),
    )
    runner = NodeRunner(
        tmp_path / "work",
        python_adapters={"tests.adapters:rejected": adapter},
        validators={
            "tests.validators:reject": lambda _context: NodeValidatorResult(
                passed=False, message="synthetic rejection"
            )
        },
    )
    with pytest.raises(RunnerError) as captured:
        runner.run_automatic(request_for(definition))
    assert captured.value.code == "E_RUNNER_VALIDATION_REJECTED"
    assert captured.value.reason is RunnerFailureReason.VALIDATOR_FAILED
