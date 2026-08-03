"""Permission-enforced runtime registration for extension contributions.

Extensions may execute Python code, but Core only consumes commands,
extractors and validators that pass through this registrar.  The registrar
checks both the manifest's declared contribution names and its permissions;
it therefore forms the Core service boundary instead of trusting a mutable
``ExtensionContext.permissions`` hint.
"""

from __future__ import annotations

from dataclasses import dataclass, field


class ContributionRegistrationError(PermissionError):
    """Raised when an extension attempts an undeclared contribution."""


@dataclass
class ExtensionRegistrar:
    """Collect typed contributions for one activation before committing them."""

    extension_id: str
    version: str
    permissions: frozenset[str]
    declared_contributions: dict[str, object]
    commands: dict[str, object] = field(default_factory=dict)
    extractors: dict[str, object] = field(default_factory=dict)
    validators: dict[str, object] = field(default_factory=dict)

    def register_command(self, name: str, handler: object) -> None:
        """Register a command handler declared in the extension manifest."""
        self._authorize("commands", name, "register_commands")
        if not callable(handler):
            raise TypeError("command handler must be callable")
        self.commands[name] = handler

    def register_extractor(self, source_format: str, extractor: object) -> None:
        """Register an extractor declared for *source_format*."""
        self._authorize("extractors", source_format, "register_extractors")
        if not callable(getattr(extractor, "extract", None)):
            raise TypeError("extractor must provide an extract() method")
        self.extractors[source_format] = extractor

    def register_validator(self, check_id: str, validator: object) -> None:
        """Register a validator declared by its stable check ID."""
        self._authorize("validators", check_id, "register_validators")
        if not callable(getattr(validator, "run", None)):
            raise TypeError("validator must provide a run() method")
        self.validators[check_id] = validator

    def _authorize(self, kind: str, name: str, permission: str) -> None:
        if permission not in self.permissions:
            raise ContributionRegistrationError(
                f"extension {self.extension_id!r} lacks {permission!r}"
            )
        raw = self.declared_contributions.get(kind, [])
        declared = {
            item for item in raw if isinstance(item, str)
        } if isinstance(raw, list) else set()
        if name not in declared:
            raise ContributionRegistrationError(
                f"extension {self.extension_id!r} did not declare {kind} "
                f"contribution {name!r}"
            )


class ExtensionContributionRegistry:
    """Core-owned, process-local view of active extension contributions."""

    def __init__(self) -> None:
        self._commands: dict[str, tuple[str, object]] = {}
        self._extractors: dict[str, tuple[str, object]] = {}
        self._validators: dict[str, tuple[str, object]] = {}

    def registrar_for(
        self,
        *,
        extension_id: str,
        version: str,
        permissions: frozenset[str],
        declared_contributions: dict[str, object],
    ) -> ExtensionRegistrar:
        """Create an isolated registrar for an activation attempt."""
        return ExtensionRegistrar(
            extension_id=extension_id,
            version=version,
            permissions=permissions,
            declared_contributions=declared_contributions,
        )

    def commit(self, registrar: ExtensionRegistrar) -> None:
        """Atomically replace one extension's contributions after activation."""
        commands = self._without_owner(self._commands, registrar.extension_id)
        extractors = self._without_owner(self._extractors, registrar.extension_id)
        validators = self._without_owner(self._validators, registrar.extension_id)
        self._check_conflicts(commands, registrar.commands, "command")
        self._check_conflicts(extractors, registrar.extractors, "extractor")
        self._check_conflicts(validators, registrar.validators, "validator")
        commands.update(
            {
                name: (registrar.extension_id, handler)
                for name, handler in registrar.commands.items()
            }
        )
        extractors.update(
            {
                name: (registrar.extension_id, extractor)
                for name, extractor in registrar.extractors.items()
            }
        )
        validators.update(
            {
                name: (registrar.extension_id, validator)
                for name, validator in registrar.validators.items()
            }
        )
        self._commands = commands
        self._extractors = extractors
        self._validators = validators

    @property
    def commands(self) -> dict[str, object]:
        """Return the active command handlers by stable command name."""
        return {name: item[1] for name, item in self._commands.items()}

    @property
    def extractors(self) -> dict[str, object]:
        """Return the active extension extractors by source-format name."""
        return {name: item[1] for name, item in self._extractors.items()}

    @property
    def validators(self) -> dict[str, object]:
        """Return the active extension validators by stable check ID."""
        return {name: item[1] for name, item in self._validators.items()}

    @staticmethod
    def _without_owner(
        entries: dict[str, tuple[str, object]], extension_id: str
    ) -> dict[str, tuple[str, object]]:
        return {
            name: item
            for name, item in entries.items()
            if item[0] != extension_id
        }

    @staticmethod
    def _check_conflicts(
        existing: dict[str, tuple[str, object]],
        incoming: dict[str, object],
        kind: str,
    ) -> None:
        collisions = sorted(set(existing) & set(incoming))
        if collisions:
            raise ContributionRegistrationError(
                f"{kind} contribution(s) already owned by another extension: "
                f"{', '.join(collisions)}"
            )


__all__ = [
    "ContributionRegistrationError",
    "ExtensionContributionRegistry",
    "ExtensionRegistrar",
]
