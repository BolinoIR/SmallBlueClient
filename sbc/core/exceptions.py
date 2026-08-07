class SBCError(Exception):
    """Base SmallBlueClient error."""

class SessionError(SBCError):
    """An .sbc file is malformed, corrupt, or unsupported."""

class ConnectionError(SBCError):
    """The BBB GraphQL endpoint could not be reached."""

class GraphQLError(SBCError):
    """BigBlueButton returned GraphQL errors."""

class PermissionDenied(GraphQLError):
    """The saved BBB role cannot perform this action."""

class MutationNotFound(SBCError):
    """The requested operation is not in SBC's BBB action registry."""

class MutationValidationError(SBCError):
    """Mutation arguments do not match the BBB GraphQL action schema."""
