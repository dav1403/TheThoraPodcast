import sys
import os
import subprocess

print("--- DEBUG INFO ---")
print(f"Python Version: {sys.version}")
print(f"Executable Path: {sys.executable}")
print(f"Search Paths (sys.path): {sys.path}")

try:
    import requests
    print("SUCCESS: 'requests' module found.")
    print(f"Requests location: {requests.__file__}")
except ImportError:
    print("FAILURE: 'requests' module NOT found.")
    print("\nAttempting to list installed packages via pip...")
    try:
        result = subprocess.run([sys.executable, '-m', 'pip', 'list'], capture_output=True, text=True)
        print(result.stdout)
    except Exception as e:
        print(f"Could not run pip list: {e}")

# This will prevent the workflow from failing just yet so we can see the output
print("--- END DEBUG INFO ---")
