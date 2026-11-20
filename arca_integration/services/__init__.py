"""Frappe-independent ARCA logic.

Nothing in this package imports frappe: every module takes plain values or
dataclasses and returns plain values, so it can be unit-tested and replaced
without touching DocTypes. Frappe adapters live next to the DocTypes.
"""
