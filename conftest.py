"""
conftest.py — pytest configuration at the project root.

Adds the project root to sys.path so that test files can import
protocol, auth, clients, sessions etc. without a package install.
"""
import os
import sys

# Ensure the project root is first on the path.
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))
