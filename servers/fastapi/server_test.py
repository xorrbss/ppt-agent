import os
import uvicorn
from dotenv import load_dotenv

load_dotenv()

if __name__ == "__main__":
    os.makedirs("debug", exist_ok=True)

    uvicorn.run(
        "api.main:app", host="0.0.0.0", port=8000, log_level="info", reload=True
    )
