"""
PM Orchestrator entrypoint — runs the JSON-RPC server on port 8001.
"""

import uvicorn

if __name__ == "__main__":
    uvicorn.run(
        "pm_orchestrator.rpc:app",
        host="0.0.0.0",
        port=8001,
        reload=False,
    )
