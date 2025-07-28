import uvicorn
import argparse

from services.icon_finder_service import IconFinderService


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run the FastAPI server")
    parser.add_argument(
        "--port", type=int, required=True, help="Port number to run the server on"
    )
    parser.add_argument(
        "--reload", type=bool, default=False, help="Reload the server on code changes"
    )
    args = parser.parse_args()

    # Initialize the Icons Collection
    IconFinderService()

    uvicorn.run(
        "api.main:app",
        host="0.0.0.0",
        port=args.port,
        log_level="info",
        reload=args.reload,
    )
