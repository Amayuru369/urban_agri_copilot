import gradio as gr
from backend.main import app as fastapi_app

# Gradio mounts your entire existing FastAPI application seamlessly
# This exposes all your existing routes: /, /garden, /login, /api/*
demo = gr.mount_gradio_app(fastapi_app, gr.Blocks(), path="/gradio")

# Run via Uvicorn on Hugging Face's default port 7860
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(fastapi_app, host="0.0.0.0", port=7860)