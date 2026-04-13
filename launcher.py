"""Punto de entrada raiz — usado por PyInstaller para empaquetar la app."""
import sys
from app.main import main

if __name__ == "__main__":
    sys.exit(main())
