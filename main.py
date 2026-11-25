"""Application entry point; the supervisor keeps the UI independent of the worker."""
from dip_inspection.core.supervisor import main

if __name__ == "__main__":
    main()
