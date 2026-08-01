"""Core release packaging (PRD FR-12 / B2S-M5-04).

Produces the official ``book2skill-core-<semver>.zip`` delivery artifact that
downstream projects (DD Methods, DD Workbench) install against. The release
package is the only sanctioned upstream, so it bundles the Wheel, the
Book2Skill meta-Skill, public contracts, the SDK surface and an installer.
"""

from book2skill.packaging.release import ReleaseError, build_release

__all__ = ["build_release", "ReleaseError"]
