"""The observer view: a read-only, omniscient view of a game for an outside observer.

TRD §41.1 (D-122). The feed reads the game database and never writes to it; its consumers are
the terminal viewer and the publisher. It lives outside ``app/`` and has no authority over game
state.
"""
