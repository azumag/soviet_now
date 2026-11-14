"""Temporary migration shim for axis 9.3.

This module exists so the strategy refactor can be reviewed independently from
strategy.py's historically large scorer.  strategy.py should import
``strategy_helpers.reactive_pair`` directly; this shim is intentionally not a
runtime entry point.
"""

from strategy_helpers.reactive_pair import blocking_penalty

__all__ = ["blocking_penalty"]
