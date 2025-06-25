class AppBaseError(Exception):
    """Base class for all custom exceptions in the application."""
    def __init__(self, message: str, details: dict = None):
        super().__init__(message)
        self.message = message
        self.details = details or {}

class InfrastructureProvisioningError(AppBaseError):
    """Raised when infrastructure provisioning (e.g., Terraform, AWS) fails."""
    pass

class ApplicationBuildError(AppBaseError):
    """Raised when application build (e.g., Docker build) fails."""
    pass

class ConfigurationError(AppBaseError):
    """Raised for configuration issues (missing settings, invalid values)."""
    pass

class UserInputValidationError(AppBaseError):
    """Raised when user input validation fails."""
    pass
