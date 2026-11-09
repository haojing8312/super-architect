"""Public release trust installed with this product, never learned from a server.

Existing local policy overrides are retained, including an explicitly empty
policy. Only a missing settings row is populated by Store's normal migration.
"""


def default_release_trust() -> dict:
    return {
        "origin": "https://archi.taoofagi.com",
        "installer_prefix": "ArchitectureWorkbench",
        "public_keys": {"archi-20261004": "IB9/dzelswGgtYlFZIDoAcomvMiq3+jEBKIjAlNtMGU="},
    }
