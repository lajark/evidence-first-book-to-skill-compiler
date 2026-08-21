"""Windows desktop WebGUI for Book2Skill.

The desktop package is an optional presentation layer.  It delegates all
document processing to the existing application use cases and never becomes a
second source of pipeline business rules.
"""

from book2skill.desktop.runtime_paths import application_data_root

__all__ = ["application_data_root"]
