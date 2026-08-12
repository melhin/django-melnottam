"""Unit tests for the unified PoolManager covering both pool backends.

These tests mock the executor classes so they don't actually spawn
subinterpreters or subprocesses — they verify the manager's branching
logic, queue creation, and worker-submission wiring.
"""

from unittest.mock import MagicMock, patch

import pytest

from django_melnottam.config import TaskWorkerConfig
from django_melnottam.manager import (
    InterpreterPoolManager,  # backwards-compat alias
    PoolManager,
    PoolType,
    _reset_worker_signals,
)


# ----------------------------------------------------------------------
# PoolType / alias
# ----------------------------------------------------------------------
def test_pool_type_values():
    """PoolType exposes the two expected backends."""
    assert PoolType.INTERPRETER.value == "interpreter"
    assert PoolType.PROCESS.value == "process"


def test_interpreter_pool_manager_alias_is_pool_manager():
    """The legacy alias must point at the unified class."""
    assert InterpreterPoolManager is PoolManager


# ----------------------------------------------------------------------
# Initialization
# ----------------------------------------------------------------------
def test_manager_defaults_to_interpreter():
    """Without an explicit pool_type the manager uses the interpreter backend."""
    manager = PoolManager(max_workers=2)
    assert manager.pool_type is PoolType.INTERPRETER
    assert manager.max_workers == 2
    assert manager.futures == []
    assert manager.shutdown_queues == []
    assert manager._shutdown_requested is False
    assert manager._parent_shutdown_queue is None
    assert manager.executor is not None


@patch("django_melnottam.manager.ProcessPoolExecutor")
@patch("django_melnottam.manager.multiprocessing.get_context")
def test_process_manager_initialization(mock_get_context, mock_executor_class):
    """Process backend builds a fork mp_context and ProcessPoolExecutor."""
    mock_ctx = MagicMock()
    mock_get_context.return_value = mock_ctx
    mock_executor = MagicMock()
    mock_executor_class.return_value = mock_executor

    manager = PoolManager(max_workers=3, pool_type=PoolType.PROCESS)

    mock_get_context.assert_called_once_with("fork")
    mock_executor_class.assert_called_once_with(
        max_workers=3,
        mp_context=mock_ctx,
        initializer=_reset_worker_signals,
    )
    assert manager.pool_type is PoolType.PROCESS
    assert manager.executor is mock_executor
    assert manager.backend._mp_context is mock_ctx


# ----------------------------------------------------------------------
# create_queue
# ----------------------------------------------------------------------
def test_create_queue_interpreter():
    """Interpreter backend returns a concurrent.interpreters queue."""
    from concurrent.interpreters import Queue as InterpQueue

    manager = PoolManager(max_workers=1, pool_type=PoolType.INTERPRETER)
    q = manager.create_queue()
    assert isinstance(q, InterpQueue)


def test_create_queue_process():
    """Process backend returns a manager-backed queue proxy (picklable)."""
    manager = PoolManager(max_workers=1, pool_type=PoolType.PROCESS)
    q = manager.create_queue()
    assert hasattr(q, "put")
    assert hasattr(q, "get")
    # Clean up the Manager helper process
    manager.backend._mp_manager.shutdown()


# ----------------------------------------------------------------------
# start_worker / start_workers
# ----------------------------------------------------------------------
@patch("django_melnottam.manager.InterpreterPoolExecutor")
def test_start_worker_assigns_queues_and_submits(mock_executor_class):
    """start_worker creates a shutdown queue, injects it into the config,
    and submits the task function with the resolved kwargs."""
    mock_executor = MagicMock()
    mock_executor_class.return_value = mock_executor
    mock_future = MagicMock()
    mock_executor.submit.return_value = mock_future

    manager = PoolManager(max_workers=1, pool_type=PoolType.INTERPRETER)
    parent_q = manager.create_queue()
    manager.set_parent_shutdown_queue(parent_q)

    config = TaskWorkerConfig(worker_number=7, log_level=20)
    manager.start_worker(config)

    assert len(manager.shutdown_queues) == 1
    assert config.shutdown_queue is manager.shutdown_queues[0]
    assert config.parent_shutdown_queue is parent_q
    assert manager.futures == [mock_future]

    mock_executor.submit.assert_called_once()
    submitted_fn, *submitted_args = mock_executor.submit.call_args.args
    submitted_kwargs = mock_executor.submit.call_args.kwargs
    assert submitted_fn is config.get_task_function()
    assert submitted_kwargs == config.get_task_kwargs()


@patch("django_melnottam.manager.InterpreterPoolExecutor")
def test_start_workers_iterates_configs(mock_executor_class):
    """start_workers submits one task per config and tracks all futures."""
    mock_executor = MagicMock()
    mock_executor_class.return_value = mock_executor
    mock_executor.submit.side_effect = [MagicMock() for _ in range(3)]

    manager = PoolManager(max_workers=3, pool_type=PoolType.INTERPRETER)
    configs = [TaskWorkerConfig(worker_number=i + 1, log_level=20) for i in range(3)]
    manager.start_workers(configs)

    assert len(manager.futures) == 3
    assert len(manager.shutdown_queues) == 3
    assert mock_executor.submit.call_count == 3


@patch("django_melnottam.manager.InterpreterPoolExecutor")
def test_start_worker_raises_on_submit_failure(mock_executor_class):
    """A submit failure is wrapped in WorkerInitializationError."""
    from django_melnottam.exceptions import WorkerInitializationError

    mock_executor = MagicMock()
    mock_executor_class.return_value = mock_executor
    mock_executor.submit.side_effect = RuntimeError("boom")

    manager = PoolManager(max_workers=1, pool_type=PoolType.INTERPRETER)
    config = TaskWorkerConfig(worker_number=1, log_level=20)

    with pytest.raises(WorkerInitializationError):
        manager.start_worker(config)


# ----------------------------------------------------------------------
# start_web_workers socket duplication
# ----------------------------------------------------------------------
@patch("django_melnottam.manager.InterpreterPoolExecutor")
def test_start_web_workers_duplicates_sockets_per_worker(mock_executor_class):
    """Each web worker gets its own duplicated socket FDs, and the parent
    closes the originals after duplication."""
    mock_executor = MagicMock()
    mock_executor_class.return_value = mock_executor
    mock_executor.submit.return_value = MagicMock()

    manager = PoolManager(max_workers=2, pool_type=PoolType.INTERPRETER)
    manager.start_web_workers("myapp.wsgi:application", workers=2, bind="127.0.0.1:0")

    assert mock_executor.submit.call_count == 2
    submitted_kwargs = [c.kwargs for c in mock_executor.submit.call_args_list]

    # Each worker must receive a non-empty insecure_sockets tuple
    for kw in submitted_kwargs:
        assert kw["insecure_sockets"], "worker got empty insecure_sockets"

    # The duplicated FD tuples must be distinct objects per worker
    assert submitted_kwargs[0]["insecure_sockets"] is not submitted_kwargs[1]["insecure_sockets"]


# ----------------------------------------------------------------------
# shutdown
# ----------------------------------------------------------------------
@patch("django_melnottam.manager.InterpreterPoolExecutor")
def test_shutdown_skips_interpreter_cleanup_for_process(mock_executor_class):
    """The interpreter-cleanup branch must NOT run for the process backend."""
    mock_executor = MagicMock()
    mock_executor_class.return_value = mock_executor

    manager = PoolManager(max_workers=1, pool_type=PoolType.PROCESS)
    manager.shutdown_queues = [MagicMock()]
    manager.futures = []

    # The process backend has no _interpreters_to_close list at all; shutdown
    # must not raise and must not attempt any interpreter cleanup.
    assert not hasattr(manager.backend, "_interpreters_to_close")
    manager.shutdown(timeout=0.1)


@patch("django_melnottam.manager.InterpreterPoolExecutor")
def test_shutdown_runs_interpreter_cleanup_for_interpreter(mock_executor_class):
    """The interpreter-cleanup branch DOES run for the interpreter backend."""
    mock_executor = MagicMock()
    mock_executor_class.return_value = mock_executor

    manager = PoolManager(max_workers=1, pool_type=PoolType.INTERPRETER)
    mock_interp = MagicMock()
    manager.backend._interpreters_to_close = [mock_interp]
    manager.shutdown_queues = [MagicMock()]
    manager.futures = []

    manager.shutdown(timeout=0.1)
    mock_interp.close.assert_called_once()


# ----------------------------------------------------------------------
# join
# ----------------------------------------------------------------------
@patch("django_melnottam.manager.InterpreterPoolExecutor")
def test_join_waits_for_all_futures(mock_executor_class):
    """join() calls result() on every tracked future."""
    mock_executor = MagicMock()
    mock_executor_class.return_value = mock_executor

    manager = PoolManager(max_workers=2, pool_type=PoolType.INTERPRETER)
    f1, f2 = MagicMock(), MagicMock()
    manager.futures = [f1, f2]

    manager.join()

    f1.result.assert_called_once()
    f2.result.assert_called_once()


@patch("django_melnottam.manager.InterpreterPoolExecutor")
def test_join_reraises_future_failure(mock_executor_class):
    """join() must re-raise if any future raised."""
    mock_executor = MagicMock()
    mock_executor_class.return_value = mock_executor

    manager = PoolManager(max_workers=1, pool_type=PoolType.INTERPRETER)
    bad_future = MagicMock()
    bad_future.result.side_effect = RuntimeError("worker died")
    manager.futures = [bad_future]

    with pytest.raises(RuntimeError, match="worker died"):
        manager.join()
