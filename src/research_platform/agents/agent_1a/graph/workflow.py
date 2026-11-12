"""Minimal Agent 1A workflow: graph construction and technical runner.

The graph is a straight line on purpose::

    START → initialize_run → validate_runtime_state → execute_smoke_step → complete_run → END

There is no routing yet, because there is nothing to route on.  The shape exists so that
Iteration 2+ can add parse/quality/retrieval/extraction nodes without redesigning the
runtime.

Failure policy: a node that raises stops the graph.  The runner then normalises the error,
marks the run failed and re-raises — a graph never reports success after a critical error.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, cast

from langgraph.graph import END, START, StateGraph

from research_platform.agents.agent_1a.contracts.graph_state import Agent1AState
from research_platform.agents.agent_1a.graph.nodes import (
    GraphDependencies,
    complete_run,
    execute_smoke_step,
    initialize_run,
    validate_runtime_state,
)
from research_platform.agents.agent_1a.graph.state import SmokeRunRequest, build_smoke_state
from research_platform.application.ports.logging import RUNTIME_ERROR
from research_platform.application.ports.model_gateway.errors import ModelError
from research_platform.config import LogLevel
from research_platform.domain.runs.models import RunError, RunRecord, RunStage, RunStatus

__all__ = ["Agent1ASmokeGraph", "SmokeRunResult", "build_smoke_graph"]

_NODE_INITIALIZE = "initialize_run"
_NODE_VALIDATE = "validate_runtime_state"
_NODE_SMOKE = "execute_smoke_step"
_NODE_COMPLETE = "complete_run"


@dataclass(frozen=True, slots=True)
class SmokeRunResult:
    """Outcome of one technical smoke run."""

    run_id: str
    correlation_id: str
    status: RunStatus
    technical_trace_ref: str | None
    final_state: Agent1AState
    record: RunRecord


class Agent1ASmokeGraph:
    """Compiled smoke graph plus the runner that owns its lifecycle."""

    def __init__(
        self,
        *,
        graph: Any,
        dependencies: GraphDependencies,
        checkpointer: object | None = None,
        thread_id_for_run: Any | None = None,
    ) -> None:
        self._graph = graph
        self._deps = dependencies
        self._checkpointer = checkpointer
        self._thread_id_for_run = thread_id_for_run or (lambda run_id: f"thread_{run_id}")

    @property
    def graph(self) -> Any:
        """The compiled LangGraph object (exposed for health checks and tests)."""
        return self._graph

    @property
    def dependencies(self) -> GraphDependencies:
        """Dependencies injected into the nodes."""
        return self._deps

    def run(self, request: SmokeRunRequest | None = None) -> SmokeRunResult:
        """Execute the smoke workflow once.

        Raises:
            ValueError: the graph state or the run invariants were violated.
            ModelError: the configured model failed in a non-retryable way.
        """
        smoke_request = request or SmokeRunRequest()
        run_id = self._deps.run_manager.new_run_id()
        state = build_smoke_state(smoke_request, run_id=run_id)
        config: dict[str, Any] = {
            "recursion_limit": self._deps.settings.runtime.max_graph_steps * 4,
        }
        if self._checkpointer is not None:
            config["configurable"] = {"thread_id": self._thread_id_for_run(run_id)}
        try:
            final_state = self._graph.invoke(state, config=config)
        except Exception as exc:
            self._record_failure(state=state, exc=exc)
            raise

        run_id = final_state["run_id"]
        record = self._deps.run_manager.get_run(run_id)
        return SmokeRunResult(
            run_id=run_id,
            correlation_id=final_state.get("correlation_id", ""),
            status=record.status,
            technical_trace_ref=final_state.get("technical_trace_ref"),
            final_state=final_state,
            record=record,
        )

    def state_for_run(self, run_id: str) -> Agent1AState | None:
        """Read the last checkpointed state of a run (in-memory checkpointer only)."""
        if self._checkpointer is None:
            return None
        config = {"configurable": {"thread_id": self._thread_id_for_run(run_id)}}
        snapshot = self._graph.get_state(config)
        values = getattr(snapshot, "values", None)
        if not values:
            return None
        return cast(Agent1AState, values)

    # ------------------------------------------------------------------ failures

    def _record_failure(self, *, state: Agent1AState, exc: BaseException) -> None:
        """Mark the run failed and emit the runtime error event, then re-raise upstream."""
        run_id = state.get("run_id") or ""
        correlation_id = state.get("correlation_id") or f"corr_{run_id or 'unknown'}"
        log = self._deps.logger.bind(run_id=run_id or None, correlation_id=correlation_id)
        error = self._normalise(exc, correlation_id)
        if run_id:
            try:
                self._deps.run_manager.fail_run(run_id, error)
            except Exception:
                log.event(
                    RUNTIME_ERROR,
                    level=LogLevel.ERROR,
                    error_code=error.code,
                    stage=error.stage.value,
                    detail="run could not be marked failed",
                )
        log.event(
            RUNTIME_ERROR,
            level=LogLevel.ERROR,
            error_code=error.code,
            stage=error.stage.value,
            retryable=error.retryable,
            exception_type=type(exc).__name__,
        )

    @staticmethod
    def _normalise(exc: BaseException, correlation_id: str) -> RunError:
        stage = RunStage.SMOKE
        if isinstance(exc, ModelError):
            return RunError(
                code=exc.code,
                message=str(exc)[:500] or exc.code,
                stage=stage,
                retryable=exc.retryable,
                correlation_id=correlation_id,
            )
        if isinstance(exc, ValueError):
            return RunError(
                code="invalid_state",
                message=str(exc)[:500] or "invalid state",
                stage=stage,
                retryable=False,
                correlation_id=correlation_id,
            )
        return RunError(
            code="unexpected_error",
            message=f"{type(exc).__name__}: {exc}"[:500],
            stage=stage,
            retryable=False,
            correlation_id=correlation_id,
        )


def build_smoke_graph(
    dependencies: GraphDependencies,
    *,
    checkpointer: object | None = None,
    thread_id_for_run: Any | None = None,
) -> Agent1ASmokeGraph:
    """Build and compile the technical Agent 1A graph.

    Args:
        dependencies: injected services; nodes never construct their own.
        checkpointer: LangGraph-compatible saver (``InMemorySaver`` in this iteration).

    Returns:
        A runner wrapping the compiled graph.
    """

    def _initialize(state: Agent1AState) -> dict[str, Any]:
        return initialize_run(state, dependencies)

    def _validate(state: Agent1AState) -> dict[str, Any]:
        return validate_runtime_state(state, dependencies)

    def _smoke(state: Agent1AState) -> dict[str, Any]:
        return execute_smoke_step(state, dependencies)

    def _complete(state: Agent1AState) -> dict[str, Any]:
        return complete_run(state, dependencies)

    builder = StateGraph(Agent1AState)  # ty: ignore[invalid-argument-type] - TypedDict state
    builder.add_node(_NODE_INITIALIZE, _initialize)
    builder.add_node(_NODE_VALIDATE, _validate)
    builder.add_node(_NODE_SMOKE, _smoke)
    builder.add_node(_NODE_COMPLETE, _complete)

    builder.add_edge(START, _NODE_INITIALIZE)
    builder.add_edge(_NODE_INITIALIZE, _NODE_VALIDATE)
    builder.add_edge(_NODE_VALIDATE, _NODE_SMOKE)
    builder.add_edge(_NODE_SMOKE, _NODE_COMPLETE)
    builder.add_edge(_NODE_COMPLETE, END)

    compiled = (
        builder.compile(checkpointer=cast(Any, checkpointer))
        if checkpointer is not None
        else builder.compile()
    )
    return Agent1ASmokeGraph(
        graph=compiled,
        dependencies=dependencies,
        checkpointer=checkpointer,
        thread_id_for_run=thread_id_for_run,
    )
