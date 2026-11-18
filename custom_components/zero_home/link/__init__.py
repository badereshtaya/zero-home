"""Zero Home Link core — the tunnel to the Zero Home relay (PROTOCOL.md §2/§3).

Nothing in this package imports Home Assistant: it works with plain callables,
an aiohttp session and a local base URL, so it can be tested on its own.
Modules import each other relatively only.
"""
