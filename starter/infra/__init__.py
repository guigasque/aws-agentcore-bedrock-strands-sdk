"""Idempotent, all-code AWS provisioning for the Customer Support AI Agent.

Each module creates one layer of infrastructure (describe-or-create, no duplicates)
and exposes a matching teardown. `provision.py` / `destroy.py` orchestrate them.
No console/UI steps are required.
"""
