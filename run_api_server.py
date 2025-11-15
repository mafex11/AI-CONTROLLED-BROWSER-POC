"""Run the API server for frontend integration."""

import asyncio
import logging
import sys

from aibrowser.api_server import app
import uvicorn

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format='%(levelname)-8s | %(message)s',
    )
    
    uvicorn.run(app, host="0.0.0.0", port=8000)

