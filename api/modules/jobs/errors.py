class JobsDomainError(Exception):
    pass


class JobNotFoundError(JobsDomainError):
    pass


class ResultNotFoundError(JobsDomainError):
    pass


class CompanyUnknownError(JobsDomainError):
    pass


class ConcurrencyLimitError(JobsDomainError):
    def __init__(self, limit: int, running: int) -> None:
        super().__init__(f"limite de jobs concorrentes atingido ({running}/{limit})")
        self.limit = limit
        self.running = running


class InvalidJobStateError(JobsDomainError):
    def __init__(self, status: str) -> None:
        super().__init__(f"operação inválida para job em estado '{status}'")
        self.status = status


class RetryLimitError(JobsDomainError):
    def __init__(self, attempts: int, limit: int) -> None:
        super().__init__(f"máximo de tentativas atingido ({attempts}/{limit})")
        self.attempts = attempts
        self.limit = limit
