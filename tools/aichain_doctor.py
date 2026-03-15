#!/usr/bin/env python3
"""
aichain_doctor.py - AIchain Preflight Diagnostic Check

Evaluates whether the host is ready to run the aichaind daemon.
Validates dependencies, python versions, port bindings, and directory states.
"""

import sys
import os
import socket
from pathlib import Path

def print_result(check_name: str, passed: bool, details: str = ""):
    status = "\033[32m[PASS]\033[0m" if passed else "\033[31m[FAIL]\033[0m"
    detail_str = f" - {details}" if details else ""
    print(f"{status} {check_name}{detail_str}")

def test_python_version() -> bool:
    v = sys.version_info
    passed = v.major >= 3 and (v.major > 3 or v.minor >= 11)
    print_result("Python Version", passed, f"Found {v.major}.{v.minor}.{v.micro} (Requires >= 3.11)")
    return passed

def test_dependencies() -> bool:
    try:
        import requests
        import pytest
        print_result("Dependencies", True, "requests, pytest importable")
        return True
    except ImportError as e:
        print_result("Dependencies", False, f"Missing {e.name}. Run 'pip install -r requirements.txt'")
        return False

def test_environment_vars() -> bool:
    has_openrouter = bool(os.environ.get("OPENROUTER_KEY"))
    has_gemini = bool(os.environ.get("GEMINI_KEY"))
    
    if has_openrouter or has_gemini:
        print_result("API Keys", True, "Detected cloud provider keys in environment.")
        return True
    else:
        print_result("API Keys", False, "No OPENROUTER_KEY or GEMINI_KEY found. Cloud routing may failover to local.")
        return False

def test_config_paths() -> bool:
    home = Path.home()
    oc_dir = home / ".openclaw" / "aichain"
    passed = True
    
    if not oc_dir.exists():
        print_result("Data Directory", False, f"{oc_dir} does not exist. Run setup script.")
        passed = False
    elif not os.access(oc_dir, os.W_OK | os.R_OK):
        print_result("Data Directory", False, f"{oc_dir} exists but no read/write permission.")
        passed = False
    else:
        print_result("Data Directory", True, f"RW access to {oc_dir}")

    return passed

def test_port() -> bool:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(1)
    try:
        s.bind(("127.0.0.1", 8080))
        print_result("Port 8080", True, "Available for binding.")
        s.close()
        return True
    except OSError:
        # If we get OSError, port might be in use or we don't have permission.
        print_result("Port 8080", False, "Already in use or permission denied. Ensure no other instance is running.")
        return False

if __name__ == "__main__":
    print("AIchain Doctor: Preflight Checks\n" + "="*40)
    results = [
        test_python_version(),
        test_dependencies(),
        test_environment_vars(),
        test_config_paths(),
        test_port()
    ]
    
    print("="*40)
    if all([r for r in results if r is not None]): # Warning conditions like API keys shouldn't hard fail doctor conceptually, but here we treat it as a strictly typed boolean for simplicity
        print("Doctor claims this node is \033[32mREADY\033[0m to run aichaind.")
    else:
        print("Doctor claims this node is \033[33mDEGRADED\033[0m or not fully ready.")
