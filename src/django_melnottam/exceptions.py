class WorkerException(Exception):
    """Base exception for worker-related errors."""

    pass


class WorkerInitializationError(WorkerException):
    """Raised when a worker fails to initialize."""

    pass


class WorkerShutdownError(WorkerException):
    """Raised when worker shutdown fails."""

    pass


class ConfigurationError(WorkerException):
    """Raised when worker configuration is invalid."""

    pass


class HookExecutionError(WorkerException):
    """Raised when a hook fails during execution."""

    pass


class TaskExecutionError(WorkerException):
    """Raised when a task fails during execution."""

    pass


class PoolExecutionError(WorkerException):
    """Raised when the interpreter pool encounters an error."""

    pass
