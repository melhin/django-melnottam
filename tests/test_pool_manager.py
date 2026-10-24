"""Unit tests for the unified PoolManager covering both pool backends.

These tests mock the executor classes so they don't actually spawn
subinterpreters or subprocesses — they verify the manager's branching
logic, queue creation, and worker-submission wiring.
"""

from multiprocessing.managers import BaseProxy
from unittest.mock import MagicMock, patch

import pytest

from django_melnottam.backends import (
    InterpreterBackend,
    PoolType,
    ProcessBackend,
    _reset_worker_signals,
)
from django_melnottam.config import CommandWorkerConfig
from django_melnottam.manager import PoolManager, Worker
from django_melnottam.tasks import command_worker_task

# ----------------------------------------------------------------------
# Initialization
# ----------------------------------------------------------------------


def test_interpreter_manager_initialization():
    manager = PoolManager(max_workers=2, pool_type=PoolType.INTERPRETER)
    assert manager.pool_type is PoolType.INTERPRETER
    assert manager.max_workers == 2
    assert manager.workers == []
    assert manager._shutdown_requested is False
    assert manager.parent_queue is not None
    assert manager.executor is not None


@patch("django_melnottam.backends.ProcessPoolExecutor")
@patch("django_melnottam.backends.multiprocessing.get_context")
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
    assert isinstance(manager.backend, ProcessBackend)
    assert manager.backend._mp_context is mock_ctx


@patch("django_melnottam.backends.InterpreterPoolExecutor")
def test_context_manager_shuts_down_on_exit(mock_executor_class):
    """Exiting the context manager shuts the executor down."""
    mock_executor = MagicMock()
    mock_executor_class.return_value = mock_executor

    with PoolManager(max_workers=2, pool_type=PoolType.INTERPRETER) as manager:
        assert manager is not None
        assert manager.max_workers == 2

    mock_executor.shutdown.assert_called_once()


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
    # Clean up the Manager helper process
    assert isinstance(manager.backend, ProcessBackend)
    # Manager queues are proxies, not queue.Queue instances; the proxy is what
    # makes the queue picklable so it can be passed to executor.submit().
    assert isinstance(q, BaseProxy)
    manager.backend._mp_manager.shutdown()


@patch("django_melnottam.backends.InterpreterPoolExecutor")
def test_start_worker_assigns_queues_and_submits(mock_executor_class):
    """start_worker creates a shutdown queue, injects it into the config,
    and submits the task function with the resolved kwargs."""
    mock_executor = MagicMock()
    mock_executor_class.return_value = mock_executor
    mock_future = MagicMock()
    mock_executor.submit.return_value = mock_future

    manager = PoolManager(max_workers=1, pool_type=PoolType.INTERPRETER)

    config = CommandWorkerConfig(
        worker_number=7,
        log_level=20,
        command="myapp.management.commands.send_emails",
    )
    manager.start_worker(config)

    assert len(manager.workers) == 1
    assert config.shutdown_queue is manager.workers[0].shutdown_queue
    assert config.shutdown_notification_queue is manager.parent_queue
    assert manager.workers[0].future is mock_future

    mock_executor.submit.assert_called_once()
    submitted_fn, *submitted_args = mock_executor.submit.call_args.args
    submitted_kwargs = mock_executor.submit.call_args.kwargs
    assert submitted_fn is command_worker_task
    assert submitted_kwargs == config.get_task_kwargs()


@patch("django_melnottam.backends.InterpreterPoolExecutor")
def test_start_workers_iterates_configs(mock_executor_class):
    """start_workers submits one task per config and tracks all futures."""
    mock_executor = MagicMock()
    mock_executor_class.return_value = mock_executor
    mock_executor.submit.side_effect = [MagicMock() for _ in range(3)]

    manager = PoolManager(max_workers=3, pool_type=PoolType.INTERPRETER)
    configs = [
        CommandWorkerConfig(
            worker_number=i + 1,
            log_level=20,
            command="myapp.management.commands.send_emails",
        )
        for i in range(3)
    ]
    manager.start_workers(configs)

    assert len(manager.workers) == 3
    assert mock_executor.submit.call_count == 3


@patch("django_melnottam.backends.InterpreterPoolExecutor")
def test_start_worker_raises_on_submit_failure(mock_executor_class):
    """A submit failure is wrapped in WorkerInitializationError."""
    from django_melnottam.exceptions import WorkerInitializationError

    mock_executor = MagicMock()
    mock_executor_class.return_value = mock_executor
    mock_executor.submit.side_effect = RuntimeError("boom")

    manager = PoolManager(max_workers=1, pool_type=PoolType.INTERPRETER)
    config = CommandWorkerConfig(
        worker_number=1,
        log_level=20,
        command="myapp.management.commands.send_emails",
    )

    with pytest.raises(WorkerInitializationError):
        manager.start_worker(config)


# ----------------------------------------------------------------------
# start_web_workers socket duplication
# ----------------------------------------------------------------------
@patch("django_melnottam.backends.InterpreterPoolExecutor")
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
def test_shutdown_skips_interpreter_cleanup_for_process():
    manager = PoolManager(max_workers=1, pool_type=PoolType.PROCESS)
    manager.workers = [
        Worker(
            CommandWorkerConfig(
                worker_number=1,
                log_level=20,
                command="myapp.management.commands.send_emails",
            ),
            MagicMock(),
            MagicMock(),
        )
    ]

    # The process backend has no _interpreters_to_close list at all; shutdown
    # must not raise and must not attempt any interpreter cleanup.
    assert isinstance(manager.backend, ProcessBackend)
    assert not hasattr(manager.backend, "_interpreters_to_close")
    manager.shutdown(timeout=0.1)


@patch("django_melnottam.backends.InterpreterPoolExecutor")
def test_shutdown_runs_interpreter_cleanup_for_interpreter(mock_executor_class):
    """The interpreter-cleanup branch DOES run for the interpreter backend."""
    mock_executor = MagicMock()
    mock_executor_class.return_value = mock_executor

    manager = PoolManager(max_workers=1, pool_type=PoolType.INTERPRETER)
    mock_interp = MagicMock()
    assert isinstance(manager.backend, InterpreterBackend)
    manager.backend._interpreters_to_close = [mock_interp]
    manager.workers = [
        Worker(
            CommandWorkerConfig(
                worker_number=1,
                log_level=20,
                command="myapp.management.commands.send_emails",
            ),
            MagicMock(),
            MagicMock(),
        )
    ]

    manager.shutdown(timeout=0.1)
    mock_interp.close.assert_called_once()


@patch("django_melnottam.backends.multiprocessing.Manager")
@patch("django_melnottam.backends.multiprocessing.get_context")
@patch("django_melnottam.backends.ProcessPoolExecutor")
def test_shutdown_runs_process_cleanup_for_process(
    mock_executor_class, mock_get_context, mock_manager_factory
):
    mock_executor = MagicMock()
    mock_executor_class.return_value = mock_executor
    mock_mp_manager = MagicMock()
    mock_manager_factory.return_value = mock_mp_manager

    manager = PoolManager(max_workers=1, pool_type=PoolType.PROCESS)
    assert isinstance(manager.backend, ProcessBackend)
    manager.workers = [
        Worker(
            CommandWorkerConfig(
                worker_number=1,
                log_level=20,
                command="myapp.management.commands.send_emails",
            ),
            MagicMock(),
            MagicMock(),
        )
    ]

    manager.shutdown(timeout=0.1)

    mock_executor.terminate_workers.assert_called_once()
    mock_mp_manager.shutdown.assert_called_once()


@patch("django_melnottam.backends.InterpreterPoolExecutor")
def test_shutdown_sends_stop_signals(mock_executor_class):
    """shutdown() puts 'stop' on every worker queue and waits on its future."""
    mock_executor = MagicMock()
    mock_executor_class.return_value = mock_executor
    mock_future = MagicMock()

    manager = PoolManager(max_workers=2, pool_type=PoolType.INTERPRETER)

    mock_queue1 = MagicMock()
    mock_queue2 = MagicMock()
    manager.workers = [
        Worker(
            CommandWorkerConfig(
                worker_number=1,
                log_level=20,
                command="myapp.management.commands.send_emails",
            ),
            mock_future,
            mock_queue1,
        ),
        Worker(
            CommandWorkerConfig(
                worker_number=2,
                log_level=20,
                command="myapp.management.commands.send_emails",
            ),
            mock_future,
            mock_queue2,
        ),
    ]

    manager.shutdown(timeout=1.0)

    mock_queue1.put.assert_called_once_with("stop")
    mock_queue2.put.assert_called_once_with("stop")
    assert mock_future.result.call_count == 2


@patch("django_melnottam.backends.InterpreterPoolExecutor")
def test_shutdown_idempotent(mock_executor_class):
    """Calling shutdown() twice only shuts the executor down once."""
    mock_executor = MagicMock()
    mock_executor_class.return_value = mock_executor

    manager = PoolManager(max_workers=1, pool_type=PoolType.INTERPRETER)
    manager.workers = [
        Worker(
            CommandWorkerConfig(
                worker_number=1,
                log_level=20,
                command="myapp.management.commands.send_emails",
            ),
            MagicMock(),
            MagicMock(),
        )
    ]

    manager.shutdown(timeout=1.0)
    first_call_count = mock_executor.shutdown.call_count

    manager.shutdown(timeout=1.0)

    assert mock_executor.shutdown.call_count == first_call_count


# ----------------------------------------------------------------------
# join
# ----------------------------------------------------------------------
@patch("django_melnottam.backends.InterpreterPoolExecutor")
def test_join_waits_for_all_futures(mock_executor_class):
    """join() calls result() on every tracked future."""
    mock_executor = MagicMock()
    mock_executor_class.return_value = mock_executor

    manager = PoolManager(max_workers=2, pool_type=PoolType.INTERPRETER)
    f1, f2 = MagicMock(), MagicMock()
    manager.workers = [
        Worker(
            CommandWorkerConfig(
                worker_number=1,
                log_level=20,
                command="myapp.management.commands.send_emails",
            ),
            f1,
            MagicMock(),
        ),
        Worker(
            CommandWorkerConfig(
                worker_number=2,
                log_level=20,
                command="myapp.management.commands.send_emails",
            ),
            f2,
            MagicMock(),
        ),
    ]

    manager.join()

    f1.result.assert_called_once()
    f2.result.assert_called_once()


@patch("django_melnottam.backends.InterpreterPoolExecutor")
def test_join_reraises_future_failure(mock_executor_class):
    """join() must re-raise if any future raised."""
    mock_executor = MagicMock()
    mock_executor_class.return_value = mock_executor

    manager = PoolManager(max_workers=1, pool_type=PoolType.INTERPRETER)
    bad_future = MagicMock()
    bad_future.result.side_effect = RuntimeError("worker died")
    manager.workers = [
        Worker(
            CommandWorkerConfig(
                worker_number=1,
                log_level=20,
                command="myapp.management.commands.send_emails",
            ),
            bad_future,
            MagicMock(),
        )
    ]

    with pytest.raises(RuntimeError, match="worker died"):
        manager.join()
