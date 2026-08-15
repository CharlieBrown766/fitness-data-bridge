"""Connector error hierarchy."""


class FitnessConnectorError(RuntimeError):
    """Base error for user-facing connector failures."""


class ValidationError(FitnessConnectorError):
    """Input or workspace data does not satisfy the connector contract."""


class WriteGateError(FitnessConnectorError):
    """A required external-write safety gate did not pass."""


# Compatibility name used by the imported personal-data adapters.
FitnessAgentError = FitnessConnectorError
