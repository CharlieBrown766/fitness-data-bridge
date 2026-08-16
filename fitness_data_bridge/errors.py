"""Fitness Data Bridge error hierarchy."""


class FitnessDataBridgeError(RuntimeError):
    """Base error for user-facing bridge failures."""


class ValidationError(FitnessDataBridgeError):
    """Input or workspace data does not satisfy the bridge contract."""


class WriteGateError(FitnessDataBridgeError):
    """A required external-write safety gate did not pass."""
