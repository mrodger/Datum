#!/usr/bin/env python3
import json
import os

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from agent import run_agent

app = FastAPI(title="openai-agent")


class ChatRequest(BaseModel):
    messages: list
    model: str = "gpt-4o"


@app.get("/health")
def health():
    return {"status": "ok", "model_default": "gpt-4o"}


@app.post("/chat")
async def chat(req: ChatRequest):
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise HTTPException(500, "OPENAI_API_KEY not set in container environment")

    async def stream():
        async for event in run_agent(req.messages, req.model, api_key):
            yield f"data: {json.dumps(event)}\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream")


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8091))
    uvicorn.run("server:app", host="0.0.0.0", port=port, reload=False)
