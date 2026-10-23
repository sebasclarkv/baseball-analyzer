"""Local web entry point for Baseball Analyzer 2.0."""

import argparse
import os

import uvicorn


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Inicia Diamond IQ en la red local.")
    parser.add_argument(
        "--host",
        default=os.environ.get("BASEBALL_ANALYZER_HOST", "127.0.0.1"),
        help="127.0.0.1 para este equipo; 0.0.0.0 para permitir acceso desde la red local.",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("BASEBALL_ANALYZER_PORT", "8000")),
    )
    parser.add_argument("--reload", action="store_true", help="Recarga durante desarrollo.")
    args = parser.parse_args()
    uvicorn.run(
        "baseball_analyzer_v2.web:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
    )
