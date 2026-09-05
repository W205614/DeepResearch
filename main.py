"""Local entry point: run using this project's .venv interpreter."""
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("backend.api.app:create_app", factory=True, host="127.0.0.1", port=8000)
