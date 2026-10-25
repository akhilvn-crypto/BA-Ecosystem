"""Deterministic core for the Interactive BA plugin (standard library only)."""
import sys

sys.dont_write_bytecode = True  # set before any submodule is compiled: never write __pycache__ into a read-only or cached plugin folder
