"""ADMIN runtime entrypoint.

Admin/manual operation currently runs the WEB surface without TRADING/BATCH
schedulers. A future read-only admin policy can narrow this entrypoint without
changing the standard WEB runtime.
"""
from runtime_entrypoint import run_admin_runtime


if __name__ == "__main__":
    run_admin_runtime()
