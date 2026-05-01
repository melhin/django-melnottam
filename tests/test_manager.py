from unittest.mock import MagicMock, patch

from django_melnottam.config import TaskWorkerConfig
from django_melnottam.manager import InterpreterPoolManager


def test_manager_initialization():
    """Test that manager initializes with correct defaults."""
    manager = InterpreterPoolManager(max_workers=4)

    assert manager.max_workers == 4
    assert manager.futures == []
    assert manager.shutdown_queues == []
    assert not manager._shutdown_requested
    assert manager._parent_shutdown_queue is None
    assert manager.executor is not None


@patch("django_melnottam.manager.InterpreterPoolExecutor")
def test_start_worker_creates_queues(mock_executor_class):
    """Test that start_worker creates and assigns queues properly."""
    mock_executor = MagicMock()
    mock_executor_class.return_value = mock_executor
    mock_future = MagicMock()
    mock_executor.submit.return_value = mock_future

    manager = InterpreterPoolManager(max_workers=1)
    config = TaskWorkerConfig(worker_number=1, log_level=20)

    manager.start_worker(config)

    assert len(manager.shutdown_queues) == 1
    assert config.shutdown_queue is not None
    assert config.worker_queue is not None

    assert len(manager.futures) == 1
    assert manager.futures[0] == mock_future

    mock_executor.submit.assert_called_once()


@patch("django_melnottam.manager.InterpreterPoolExecutor")
def test_context_manager(mock_executor_class):
    """Test context manager enter/exit."""
    mock_executor = MagicMock()
    mock_executor_class.return_value = mock_executor

    with InterpreterPoolManager(max_workers=2) as manager:
        assert manager is not None
        assert manager.max_workers == 2

    mock_executor.shutdown.assert_called_once()


@patch("django_melnottam.manager.InterpreterPoolExecutor")
def test_shutdown_sends_stop_signals(mock_executor_class):
    """Test that shutdown sends stop signals to queues."""
    mock_executor = MagicMock()
    mock_executor_class.return_value = mock_executor
    mock_future = MagicMock()
    mock_executor.submit.return_value = mock_future

    manager = InterpreterPoolManager(max_workers=2)

    mock_queue1 = MagicMock()
    mock_queue2 = MagicMock()
    manager.shutdown_queues = [mock_queue1, mock_queue2]
    manager.futures = [mock_future, mock_future]

    manager.shutdown(timeout=1.0)

    mock_queue1.put.assert_called_once_with("stop")
    mock_queue2.put.assert_called_once_with("stop")

    assert mock_future.result.call_count == 2


@patch("django_melnottam.manager.InterpreterPoolExecutor")
def test_shutdown_idempotent(mock_executor_class):
    """Test that calling shutdown multiple times doesn't cause issues."""
    mock_executor = MagicMock()
    mock_executor_class.return_value = mock_executor

    manager = InterpreterPoolManager(max_workers=1)
    manager.shutdown_queues = [MagicMock()]
    manager.futures = []

    manager.shutdown(timeout=1.0)
    first_call_count = mock_executor.shutdown.call_count

    manager.shutdown(timeout=1.0)

    assert mock_executor.shutdown.call_count == first_call_count
